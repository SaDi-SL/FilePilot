import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.application_paths import ApplicationPaths, resolve_application_paths
from app.config_loader import ensure_external_config_exists
from app.product_identity import PRODUCT_IDENTITY
from app.windows_version import (
    render_pyinstaller_version_file,
    windows_version_metadata,
)


ROOT = Path(__file__).resolve().parents[1]


def load_build_module():
    spec = importlib.util.spec_from_file_location("filepilot_build", ROOT / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ApplicationPathTests(unittest.TestCase):
    def test_source_mode_retains_repository_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = resolve_application_paths(frozen=False, source_root=root)

        self.assertFalse(paths.installed)
        self.assertEqual(paths.install_root, root)
        self.assertEqual(paths.resource_root, root)
        self.assertEqual(paths.user_data_root, root)
        self.assertEqual(paths.config_file, root / "config" / "config.json")

    def test_installed_mode_uses_local_app_data_not_install_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            install = root / "Program Files" / "FilePilot"
            bundle = root / "bundle"
            local = root / "LocalAppData"
            paths = resolve_application_paths(
                frozen=True,
                executable=install / "FilePilot.exe",
                bundle_root=bundle,
                environment={"LOCALAPPDATA": str(local)},
            )

        self.assertTrue(paths.installed)
        self.assertEqual(paths.install_root, install)
        self.assertEqual(paths.resource_root, bundle)
        self.assertEqual(paths.user_data_root, local / "FilePilot")
        self.assertNotEqual(paths.config_file.parent.parent, paths.install_root)
        self.assertEqual(paths.journal_file, local / "FilePilot" / "data" / "operations.sqlite3")

    def test_managed_installed_paths_share_user_data_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = resolve_application_paths(
                frozen=True,
                executable=root / "install" / "FilePilot.exe",
                bundle_root=root / "bundle",
                environment={"LOCALAPPDATA": str(root / "local")},
            )
            managed = (
                paths.config_dir,
                paths.data_dir,
                paths.logs_dir,
                paths.reports_dir,
                paths.backups_dir,
                paths.plugins_dir,
                paths.reminders_dir,
            )

        for path in managed:
            self.assertTrue(path.is_relative_to(paths.user_data_root), path)
            self.assertFalse(path.is_relative_to(paths.install_root), path)

    def test_path_resolution_is_independent_of_current_working_directory(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as cwd:
            source_root = Path(source_dir).resolve()
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                paths = resolve_application_paths(frozen=False, source_root=source_root)
            finally:
                os.chdir(previous)

        self.assertEqual(paths.user_data_root, source_root)
        self.assertEqual(paths.resource_root, source_root)

    def test_first_run_seed_uses_sanitized_default_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resource = root / "bundle"
            user = root / "user"
            default = resource / "config" / "default_config.json"
            default.parent.mkdir(parents=True)
            default.write_text('{"first_run_completed": false}', encoding="utf-8")
            paths = ApplicationPaths(True, root / "install", resource, user)

            with patch("app.config_loader.get_application_paths", return_value=paths):
                seeded = ensure_external_config_exists()
                self.assertEqual(
                    json.loads(seeded.read_text(encoding="utf-8")),
                    {"first_run_completed": False},
                )
                seeded.write_text('{"preserved": true}', encoding="utf-8")
                ensure_external_config_exists()

            self.assertEqual(seeded.read_text(encoding="utf-8"), '{"preserved": true}')


class ReleaseIdentityTests(unittest.TestCase):
    def test_display_identity_remains_authoritative_development_version(self):
        self.assertEqual(PRODUCT_IDENTITY.version, "1.1.0+development")
        self.assertEqual(PRODUCT_IDENTITY.display_version, "1.1.0+development")

    def test_windows_version_mapping_is_valid_and_deterministic(self):
        metadata = windows_version_metadata()
        self.assertEqual(metadata.numeric, (1, 1, 0, 1))
        self.assertEqual(metadata.numeric_string, "1.1.0.1")
        self.assertTrue(all(0 <= value <= 65535 for value in metadata.numeric))

    def test_windows_resource_uses_identity_and_omits_unverified_company(self):
        resource = render_pyinstaller_version_file()
        self.assertIn("ProductVersion', '1.1.0+development'", resource)
        self.assertIn("FileVersion', '1.1.0.1'", resource)
        self.assertIn("OriginalFilename', 'FilePilot.exe'", resource)
        self.assertNotIn("CompanyName", resource)

    def test_build_metadata_has_no_competing_hard_coded_version(self):
        build_source = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("PRODUCT_IDENTITY.display_version", build_source)
        self.assertNotIn('APP_VERSION = "', build_source)
        self.assertNotIn("1.0.0", build_source)


class PackagingInputTests(unittest.TestCase):
    def setUp(self):
        self.build = load_build_module()
        self.spec_source = (ROOT / "FilePilot.spec").read_text(encoding="utf-8")
        self.installer_source = (ROOT / "installer.iss").read_text(encoding="utf-8")
        self.defaults = json.loads(
            (ROOT / "config" / "default_config.json").read_text(encoding="utf-8")
        )

    def test_release_input_validation_passes(self):
        self.assertEqual(self.build.validate_release_inputs(), [])

    def test_live_config_is_not_a_packaging_input(self):
        self.assertNotIn(ROOT / "config" / "config.json", self.build.PACKAGED_DATA_FILES)
        self.assertNotIn('"config" / "config.json"', self.spec_source)
        self.assertNotIn("config\\config.json", self.installer_source.lower())

    def test_build_validation_does_not_mutate_live_config(self):
        config_path = ROOT / "config" / "config.json"
        before = config_path.read_bytes()
        self.build.validate_release_inputs()
        self.assertEqual(config_path.read_bytes(), before)

    def test_release_fingerprint_excludes_live_and_runtime_data(self):
        release_files = set(self.build.release_source_files())
        self.assertNotIn(ROOT / "config" / "config.json", release_files)
        self.assertFalse(any("backups" in path.parts for path in release_files))
        self.assertFalse(any("tests" in path.parts for path in release_files))
        self.assertEqual(len(self.build.release_source_fingerprint()), 64)

    def test_sanitized_defaults_have_no_machine_or_user_paths(self):
        serialized = json.dumps(self.defaults)
        self.assertEqual(self.defaults["source_folder"], "incoming")
        self.assertEqual(self.defaults["organized_base_folder"], "organized")
        self.assertFalse(self.defaults["first_run_completed"])
        self.assertNotRegex(serialized, r"[A-Za-z]:[\\/]Users[\\/]")

    def test_sanitized_defaults_have_no_claude_or_api_secret(self):
        self.assertEqual(self.defaults["ai"]["claude_api_key"], "")
        self.assertNotIn("sk-ant-", json.dumps(self.defaults).lower())

    def test_runtime_requirements_include_supported_document_extractors(self):
        requirements = {
            line.strip().split(";", 1)[0]
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue(any(item.startswith("python-docx") for item in requirements))
        self.assertTrue(any(item.startswith("pypdf") for item in requirements))
        self.assertTrue(any(item.startswith("pypdfium2") for item in requirements))
        self.assertTrue(any(item.startswith("openpyxl") for item in requirements))

    def test_spec_packages_only_sanitized_config_and_icon_data(self):
        self.assertIn('"default_config.json"', self.spec_source)
        self.assertIn('"icon.ico"', self.spec_source)
        self.assertNotIn('ROOT / "tests"', self.spec_source)
        self.assertNotIn('ROOT / "backups"', self.spec_source)
        self.assertNotIn('ROOT / "logs"', self.spec_source)
        self.assertNotIn('ROOT / "reports"', self.spec_source)

    def test_sensitive_and_development_paths_are_declared_excluded(self):
        excluded = self.build.EXCLUDED_DISTRIBUTION_PATHS
        for expected in (
            "config/config.json",
            "backups/",
            "tests/",
            "opencode.json",
            "*.diff",
            "*.patch",
            ".env",
            ".git/",
            "logs/",
            "reports/",
            "operations.sqlite3",
        ):
            self.assertIn(expected, excluded)

    def test_spec_is_qt_windowed_and_avoids_large_unused_qt_modules(self):
        self.assertIn('ROOT / "app" / "packaged_entry.py"', self.spec_source)
        self.assertIn("console=False", self.spec_source)
        self.assertIn('"platforms/qwindows.dll"', self.spec_source)
        self.assertIn('"imageformats/qico.dll"', self.spec_source)
        self.assertIn('"PySide6.QtWebEngineWidgets"', self.spec_source)
        self.assertIn('"PySide6.QtTest"', self.spec_source)

    def test_packaged_entry_targets_qt_and_has_no_git_dependency(self):
        entry = (ROOT / "app" / "packaged_entry.py").read_text(encoding="utf-8")
        self.assertIn("from app.ui.qt.application import run_qt", entry)
        self.assertIn("ensure_external_config_exists()", entry)
        self.assertNotIn("app.gui", entry)
        self.assertNotIn("subprocess", entry)
        self.assertNotIn("git", entry.lower())

    def test_legacy_source_entry_remains_tk_compatible(self):
        source_entry = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("from app.gui import launch_gui", source_entry)
        self.assertIn("from app.headless import run_headless", source_entry)

    def test_build_outputs_are_controlled_and_cwd_independent(self):
        self.assertEqual(self.build.ROOT, ROOT)
        self.assertEqual(self.build.BUILD_DIR, ROOT / "build")
        self.assertEqual(self.build.DIST_DIR, ROOT / "dist")
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, str(ROOT / "build.py"), "--version"],
                cwd=temp_dir,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.1.0+development", result.stdout)
        self.assertIn("1.1.0.1", result.stdout)


class InstallerContractTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "installer.iss").read_text(encoding="utf-8")

    def test_installer_identity_is_supplied_by_build_metadata(self):
        self.assertIn("#ifndef AppVersion", self.source)
        self.assertIn("#ifndef AppNumericVersion", self.source)
        self.assertIn("AppVersion={#AppVersion}", self.source)
        self.assertIn("VersionInfoVersion={#AppNumericVersion}", self.source)
        self.assertNotIn("1.0.0", self.source)

    def test_installer_has_stable_upgrade_identity(self):
        self.assertIn("AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}", self.source)

    def test_installer_is_per_user_without_elevation_override(self):
        self.assertIn("DefaultDirName={localappdata}\\Programs\\{#AppName}", self.source)
        self.assertIn("PrivilegesRequired=lowest", self.source)
        self.assertNotIn("PrivilegesRequiredOverridesAllowed", self.source)

    def test_installer_shortcuts_are_intentional(self):
        self.assertIn('Name: "{group}\\{#AppName}"', self.source)
        self.assertIn("desktopicon", self.source)
        self.assertIn("Flags: unchecked", self.source)
        self.assertNotIn("Quick Launch", self.source)
        self.assertNotIn("startupicon", self.source)
        self.assertNotIn("(Headless)", self.source)

    def test_uninstall_does_not_declare_user_data_deletion(self):
        self.assertNotIn("[UninstallDelete]", self.source)
        self.assertNotIn("{localappdata}\\FilePilot", self.source)
        self.assertNotIn("config\\config.json", self.source.lower())

    def test_installer_has_no_fake_publisher_or_developer_path(self):
        self.assertNotIn("AppPublisher=", self.source)
        self.assertNotIn("AppPublisherURL=", self.source)
        self.assertNotRegex(self.source, r"[A-Za-z]:\\")


@unittest.skipUnless(os.name == "nt", "Inno Setup discovery is Windows-only")
class InnoSetupDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.build = load_build_module()
        self.usable = lambda _candidate: True

    @staticmethod
    def _compiler(root: Path) -> Path:
        compiler = root / "Inno Setup 6" / "ISCC.exe"
        compiler.parent.mkdir(parents=True)
        compiler.write_bytes(b"test compiler")
        compiler.with_name("ISCmplr.dll").write_bytes(b"test compiler library")
        compiler.with_name("ISPP.dll").write_bytes(b"test preprocessor library")
        return compiler

    def test_path_discovery_has_first_preference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path_compiler = self._compiler(root / "PATH Tools")
            user_compiler = self._compiler(root / "Local App Data" / "Programs")

            result = self.build.find_inno_setup(
                environment={
                    "PATH": str(path_compiler.parent),
                    "LOCALAPPDATA": str(root / "Local App Data"),
                },
                candidate_validator=self.usable,
            )

        self.assertEqual(result, path_compiler)
        self.assertNotEqual(result, user_compiler)

    def test_invalid_path_candidate_falls_back_to_per_user_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_compiler = self._compiler(root / "Local App Data" / "Programs")

            result = self.build.find_inno_setup(
                environment={
                    "PATH": str(root / "missing"),
                    "LOCALAPPDATA": str(root / "Local App Data"),
                },
                candidate_validator=self.usable,
            )

        self.assertEqual(result, user_compiler)

    def test_per_user_install_is_found_without_path_and_handles_spaces(self):
        with tempfile.TemporaryDirectory(prefix="FilePilot Inno Discovery ") as temp_dir:
            local_app_data = Path(temp_dir) / "Local App Data"
            compiler = self._compiler(local_app_data / "Programs")

            result = self.build.find_inno_setup(
                environment={"LOCALAPPDATA": str(local_app_data)},
                candidate_validator=self.usable,
            )

        self.assertEqual(result, compiler)
        self.assertIn(" ", str(result))

    def test_native_machine_wide_install_is_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            program_files = Path(temp_dir) / "Program Files"
            compiler = self._compiler(program_files)

            result = self.build.find_inno_setup(
                environment={"ProgramFiles": str(program_files)},
                candidate_validator=self.usable,
            )

        self.assertEqual(result, compiler)

    def test_per_user_precedes_machine_wide_locations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_app_data = root / "Local App Data"
            program_files = root / "Program Files"
            user_compiler = self._compiler(local_app_data / "Programs")
            self._compiler(program_files)

            result = self.build.find_inno_setup(
                environment={
                    "LOCALAPPDATA": str(local_app_data),
                    "ProgramFiles": str(program_files),
                },
                candidate_validator=self.usable,
            )

        self.assertEqual(result, user_compiler)

    def test_x86_machine_wide_install_is_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            program_files_x86 = Path(temp_dir) / "Program Files (x86)"
            compiler = self._compiler(program_files_x86)

            result = self.build.find_inno_setup(
                environment={"ProgramFiles(x86)": str(program_files_x86)},
                candidate_validator=self.usable,
            )

        self.assertEqual(result, compiler)

    def test_no_compiler_preserves_clear_failure_behavior(self):
        messages = []
        with (
            patch.object(self.build, "find_inno_setup", return_value=None),
            patch.object(self.build, "log", side_effect=messages.append),
        ):
            result = self.build.build_installer(required=True)

        self.assertFalse(result)
        self.assertTrue(any("compiler is unavailable" in message for message in messages))

    def test_unlaunchable_path_candidate_falls_back_to_standard_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path_compiler = self._compiler(root / "PATH Tools")
            user_compiler = self._compiler(root / "Local App Data" / "Programs")

            result = self.build.find_inno_setup(
                environment={
                    "PATH": str(path_compiler.parent),
                    "LOCALAPPDATA": str(root / "Local App Data"),
                },
                candidate_validator=lambda candidate: candidate == user_compiler,
            )

        self.assertEqual(result, user_compiler)

    def test_default_validator_rejects_non_executable_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"not a Windows executable")
            candidate.with_name("ISCmplr.dll").write_bytes(b"test compiler library")
            candidate.with_name("ISPP.dll").write_bytes(b"test preprocessor library")

            with patch.object(self.build.subprocess, "run", side_effect=OSError):
                self.assertFalse(self.build._is_usable_inno_compiler(candidate))

    def test_default_validator_accepts_successful_probe_compile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"test compiler")
            candidate.with_name("ISCmplr.dll").write_bytes(b"test compiler library")
            candidate.with_name("ISPP.dll").write_bytes(b"test preprocessor library")

            def run(command, **_kwargs):
                if "/?" in command:
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "Inno Setup 6 Command-Line Compiler\n",
                    )
                script = Path(command[-1])
                output = script.parent / "output" / "probe.exe"
                output.parent.mkdir()
                output.write_bytes(b"probe installer")
                return subprocess.CompletedProcess(command, 0, "")

            with patch.object(self.build.subprocess, "run", side_effect=run):
                result = self.build._is_usable_inno_compiler(candidate)

        self.assertTrue(result)

    def test_default_validator_rejects_old_inno_major_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"test compiler")
            candidate.with_name("ISCmplr.dll").write_bytes(b"test compiler library")
            candidate.with_name("ISPP.dll").write_bytes(b"test preprocessor library")
            completed = subprocess.CompletedProcess(
                ["ISCC.exe", "/?"],
                1,
                "Inno Setup 5 Command-Line Compiler\n",
            )
            with patch.object(self.build.subprocess, "run", return_value=completed):
                result = self.build._is_usable_inno_compiler(candidate)

        self.assertFalse(result)

    def test_default_validator_rejects_missing_compiler_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"test compiler")

            self.assertFalse(self.build._is_usable_inno_compiler(candidate))

    def test_default_validator_rejects_missing_preprocessor_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"test compiler")
            candidate.with_name("ISCmplr.dll").write_bytes(b"test compiler library")

            self.assertFalse(self.build._is_usable_inno_compiler(candidate))

    def test_default_validator_rejects_failed_probe_compile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "ISCC.exe"
            candidate.write_bytes(b"test compiler")
            candidate.with_name("ISCmplr.dll").write_bytes(b"incompatible library")
            candidate.with_name("ISPP.dll").write_bytes(b"test preprocessor library")
            help_result = subprocess.CompletedProcess(
                ["ISCC.exe", "/?"],
                1,
                "Inno Setup 6 Command-Line Compiler\n",
            )
            compile_result = subprocess.CompletedProcess(
                ["ISCC.exe", "/Qp", "probe.iss"],
                1,
                "Compiler payload could not be loaded\n",
            )
            with patch.object(
                self.build.subprocess,
                "run",
                side_effect=[help_result, compile_result],
            ):
                result = self.build._is_usable_inno_compiler(candidate)

        self.assertFalse(result)

    def test_relative_path_entries_cannot_shadow_absolute_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path_compiler = self._compiler(root / "Trusted Tools")

            result = self.build.find_inno_setup(
                environment={"PATH": f".;relative-tools;{path_compiler.parent}"},
                candidate_validator=self.usable,
            )

        self.assertEqual(result, path_compiler)

    def test_invalid_earlier_path_candidate_does_not_hide_later_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_compiler = self._compiler(root / "First Tools")
            second_compiler = self._compiler(root / "Second Tools")

            result = self.build.find_inno_setup(
                environment={
                    "PATH": os.pathsep.join(
                        (str(first_compiler.parent), str(second_compiler.parent))
                    )
                },
                candidate_validator=lambda candidate: candidate == second_compiler,
            )

        self.assertEqual(result, second_compiler)

    def test_standard_discovery_is_independent_of_current_working_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as cwd:
            local_app_data = Path(temp_dir) / "Local App Data"
            compiler = self._compiler(local_app_data / "Programs")
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                result = self.build.find_inno_setup(
                    environment={"LOCALAPPDATA": str(local_app_data)},
                    candidate_validator=self.usable,
                )
            finally:
                os.chdir(previous)

        self.assertEqual(result, compiler)


if __name__ == "__main__":
    unittest.main()
