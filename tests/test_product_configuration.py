import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import ensure_directories
from app.product_configuration import (
    ConfigurationDataState,
    ProductConfigurationCandidate,
    ProductConfigurationError,
    ProductConfigurationStore,
    ProductRule,
    ProductWatchFolder,
    StaleConfigurationError,
    configuration_revision,
    normalize_extension,
    validate_product_rules,
)


class ProductConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config_path = self.root / "config.json"
        self.incoming = self.root / "incoming"
        self.second = self.root / "second"
        self.organized = self.root / "organized"
        self.incoming.mkdir()
        self.second.mkdir()
        self.organized.mkdir()
        self.store = ProductConfigurationStore(
            self.config_path,
            path_resolver=lambda value: self.root / value,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _document(self):
        return {
            "first_run_completed": True,
            "source_folder": str(self.incoming),
            "watch_folders": [
                {
                    "path": str(self.incoming),
                    "label": "Incoming",
                    "active": True,
                }
            ],
            "organized_base_folder": str(self.organized),
            "destination_folders": {
                "documents": str(self.organized / "documents"),
                "others": str(self.organized / "others"),
            },
            "rules": {"documents": [".txt"]},
            "archive_by_date": False,
            "ai": {"enabled": False, "provider": "local"},
            "unrelated_setting": {"preserve": True},
        }

    def _write(self, document=None):
        self.config_path.write_text(
            json.dumps(document or self._document()),
            encoding="utf-8",
        )

    def _candidate(self, **overrides):
        values = {
            "watch_folders": (
                ProductWatchFolder(self.incoming, "Incoming", True),
            ),
            "organized_folder": self.organized,
            "archive_by_date": False,
            "rules": (ProductRule("documents", (".txt",)),),
        }
        values.update(overrides)
        return ProductConfigurationCandidate(**values)

    def test_missing_configuration_has_explicit_empty_state(self):
        snapshot = self.store.read_snapshot(changes_allowed=False)

        self.assertEqual(snapshot.state, ConfigurationDataState.EMPTY)
        self.assertIsNone(snapshot.revision)

    def test_malformed_json_has_explicit_invalid_state(self):
        self.config_path.write_text("{broken", encoding="utf-8")

        snapshot = self.store.read_snapshot(changes_allowed=False)

        self.assertEqual(snapshot.state, ConfigurationDataState.INVALID)
        self.assertIn("invalid", snapshot.error.lower())

    def test_invalid_document_shape_is_reported_without_escaping(self):
        self._write({"rules": []})

        snapshot = self.store.read_snapshot(changes_allowed=False)

        self.assertEqual(snapshot.state, ConfigurationDataState.INVALID)

    def test_invalid_archive_value_is_safe_to_render_and_reported(self):
        document = self._document()
        document["archive_by_date"] = "false"
        self._write(document)

        snapshot = self.store.read_snapshot(changes_allowed=True)

        self.assertEqual(snapshot.state, ConfigurationDataState.AVAILABLE)
        self.assertFalse(snapshot.folders.archive_by_date)
        self.assertIn(
            "INVALID_ARCHIVE_SETTING",
            {issue.code for issue in snapshot.issues},
        )

    def test_watch_folders_are_authoritative_when_present(self):
        document = self._document()
        document["watch_folders"] = []
        document["source_folder"] = str(self.second)

        candidate = self.store.candidate_from_document(document)

        self.assertEqual(candidate.watch_folders, ())

    def test_malformed_watch_folder_flags_are_not_silently_coerced(self):
        document = self._document()
        document["watch_folders"][0]["active"] = "false"

        with self.assertRaises(ProductConfigurationError):
            self.store.candidate_from_document(document)

    def test_empty_authoritative_watch_path_is_not_resolved_to_runtime_root(self):
        document = self._document()
        document["watch_folders"][0]["path"] = ""

        candidate = self.store.candidate_from_document(document)
        result = self.store.validate(candidate)

        self.assertEqual(candidate.watch_folders[0].path, "")
        self.assertIn("EMPTY_WATCH_FOLDER", {issue.code for issue in result.issues})

    def test_malformed_candidate_folder_flags_are_validation_errors(self):
        malformed = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.incoming, 123, "false"),
            )
        )

        result = self.store.validate(malformed)

        codes = {issue.code for issue in result.issues}
        self.assertIn("INVALID_WATCH_FOLDER_LABEL", codes)
        self.assertIn("INVALID_WATCH_FOLDER_STATE", codes)

    def test_legacy_source_folder_is_projected_when_watch_list_is_absent(self):
        document = self._document()
        document.pop("watch_folders")
        document["source_folder"] = "incoming"

        candidate = self.store.candidate_from_document(document)

        self.assertEqual(candidate.watch_folders[0].path, self.incoming)
        self.assertTrue(candidate.watch_folders[0].active)

    def test_snapshot_exposes_runtime_change_permission_and_folder_facts(self):
        self._write()

        snapshot = self.store.read_snapshot(changes_allowed=False)

        self.assertEqual(snapshot.state, ConfigurationDataState.AVAILABLE)
        self.assertFalse(snapshot.folders.changes_allowed)
        self.assertTrue(snapshot.folders.watch_folders[0].exists)
        self.assertTrue(snapshot.folders.organized_exists)

    def test_extension_normalization_accepts_supported_user_forms(self):
        for value in (".TXT", "TXT", " txt ", "*.txt"):
            with self.subTest(value=value):
                self.assertEqual(normalize_extension(value), ".txt")

    def test_extension_normalization_rejects_unsafe_forms(self):
        for value in ("", "*txt", ".tar.gz", "../txt", "a/b", None):
            with self.subTest(value=value):
                self.assertIsNone(normalize_extension(value))

    def test_duplicate_extensions_conflict_after_normalization(self):
        normalized, issues = validate_product_rules(
            (
                ProductRule("documents", ("TXT",)),
                ProductRule("notes", ("*.txt",)),
            )
        )

        self.assertEqual(normalized[0].extensions, (".txt",))
        self.assertIn("EXTENSION_CATEGORY_CONFLICT", {issue.code for issue in issues})

    def test_duplicate_extension_in_same_category_is_rejected(self):
        _, issues = validate_product_rules(
            (ProductRule("documents", ("TXT", "*.txt")),)
        )

        issue = next(issue for issue in issues if issue.code == "DUPLICATE_EXTENSION")
        self.assertEqual(len(issue.related_fields), 2)
        self.assertIn("documents", issue.message)

    def test_category_conflicts_are_case_insensitive(self):
        _, issues = validate_product_rules(
            (
                ProductRule("Documents", (".txt",)),
                ProductRule("documents", (".pdf",)),
            )
        )

        self.assertIn("DUPLICATE_CATEGORY", {issue.code for issue in issues})

    def test_unsafe_category_is_rejected(self):
        _, issues = validate_product_rules(
            (ProductRule("../escape", (".txt",)),)
        )

        self.assertIn("UNSAFE_CATEGORY", {issue.code for issue in issues})

    def test_no_active_watch_folder_is_invalid(self):
        candidate = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.incoming, "Incoming", False),
            )
        )

        result = self.store.validate(candidate)

        self.assertIn(
            "ACTIVE_WATCH_FOLDER_REQUIRED",
            {issue.code for issue in result.issues},
        )

    def test_empty_folder_values_are_rejected_before_resolution(self):
        result = self.store.validate(
            self._candidate(
                watch_folders=(ProductWatchFolder("", "Incoming", True),),
                organized_folder="",
            )
        )

        codes = {issue.code for issue in result.issues}
        self.assertIn("EMPTY_WATCH_FOLDER", codes)
        self.assertIn("EMPTY_ORGANIZED_FOLDER", codes)

    def test_inactive_missing_folder_does_not_block_other_valid_routes(self):
        candidate = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.incoming, "Incoming", True),
                ProductWatchFolder(self.root / "offline", "Offline", False),
            )
        )

        result = self.store.validate(candidate)

        self.assertNotIn(
            "WATCH_FOLDER_NOT_FOUND",
            {issue.code for issue in result.issues},
        )
        self.assertTrue(result.valid)

    def test_runtime_directory_setup_does_not_create_inactive_watch_folder(self):
        offline = self.root / "offline"
        document = self._document()
        document["watch_folders"].append(
            {"path": str(offline), "label": "Offline", "active": False}
        )
        document.update(
            {
                "log_file": str(self.root / "logs" / "filepilot.log"),
                "stats_file": str(self.root / "reports" / "stats.json"),
                "history_file": str(self.root / "reports" / "history.csv"),
                "hash_db_file": str(self.root / "reports" / "hashes.json"),
            }
        )

        ensure_directories(document)

        self.assertFalse(offline.exists())

    def test_duplicate_watch_folder_is_invalid(self):
        candidate = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.incoming, "One", True),
                ProductWatchFolder(self.incoming, "Two", True),
            )
        )

        result = self.store.validate(candidate)

        self.assertIn("DUPLICATE_WATCH_FOLDER", {issue.code for issue in result.issues})

    def test_nested_enabled_watch_folders_are_invalid(self):
        nested = self.incoming / "nested"
        nested.mkdir()
        candidate = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.incoming, "One", True),
                ProductWatchFolder(nested, "Two", True),
            )
        )

        result = self.store.validate(candidate)

        self.assertIn(
            "OVERLAPPING_WATCH_FOLDERS",
            {issue.code for issue in result.issues},
        )

    def test_organized_folder_inside_watch_folder_is_invalid(self):
        organized = self.incoming / "organized"
        organized.mkdir()

        result = self.store.validate(
            self._candidate(organized_folder=organized)
        )

        self.assertIn("UNSAFE_PATH_TOPOLOGY", {issue.code for issue in result.issues})

    def test_same_watch_and_organized_folder_is_invalid(self):
        result = self.store.validate(
            self._candidate(organized_folder=self.incoming)
        )

        self.assertIn("UNSAFE_PATH_TOPOLOGY", {issue.code for issue in result.issues})

    def test_watch_folder_inside_organized_folder_is_invalid(self):
        nested_watch = self.organized / "incoming"
        nested_watch.mkdir()

        result = self.store.validate(
            self._candidate(
                watch_folders=(
                    ProductWatchFolder(nested_watch, "Nested", True),
                )
            )
        )

        self.assertIn("UNSAFE_PATH_TOPOLOGY", {issue.code for issue in result.issues})

    @unittest.skipUnless(os.name == "nt", "Windows path comparison semantics")
    def test_windows_case_equivalent_paths_are_rejected(self):
        result = self.store.validate(
            self._candidate(organized_folder=Path(str(self.incoming).upper()))
        )

        self.assertIn("UNSAFE_PATH_TOPOLOGY", {issue.code for issue in result.issues})

    def test_candidate_preview_is_pure_and_uses_unsaved_rules(self):
        before = set(self.root.rglob("*"))

        preview = self.store.preview_classification(
            (ProductRule("portable", (" PDF ",)),),
            "Quarterly.PDF",
        )

        self.assertTrue(preview.valid)
        self.assertEqual(preview.category, "portable")
        self.assertFalse(preview.fallback)
        self.assertEqual(before, set(self.root.rglob("*")))

    def test_candidate_preview_identifies_others_fallback(self):
        preview = self.store.preview_classification(
            (ProductRule("documents", (".txt",)),),
            "archive.bin",
        )

        self.assertTrue(preview.valid)
        self.assertEqual(preview.category, "others")
        self.assertTrue(preview.fallback)

    def test_explicit_others_rule_is_distinct_from_fallback(self):
        preview = self.store.preview_classification(
            (ProductRule("Others", (".bin",)),),
            "archive.BIN",
        )

        self.assertTrue(preview.valid)
        self.assertEqual(preview.category, "others")
        self.assertFalse(preview.fallback)

    def test_invalid_candidate_preview_reports_conflict_without_writing(self):
        self._write()
        before = self.config_path.read_bytes()

        preview = self.store.preview_classification(
            (
                ProductRule("documents", ("TXT",)),
                ProductRule("notes", ("*.txt",)),
            ),
            "readme.txt",
        )

        self.assertFalse(preview.valid)
        self.assertIsNone(preview.category)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_snapshot_read_does_not_rewrite_configuration(self):
        self._write()
        before = self.config_path.read_bytes()

        first = self.store.read_snapshot(changes_allowed=True)
        second = self.store.read_snapshot(changes_allowed=False)

        self.assertEqual(first.revision, second.revision)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_document_merge_preserves_unrelated_and_ai_settings(self):
        current = self._document()
        candidate = self._candidate(
            watch_folders=(
                ProductWatchFolder(self.second, "Second", True),
                ProductWatchFolder(self.incoming, "Incoming", False),
            ),
            archive_by_date=True,
            rules=(ProductRule("media", ("MP4",)),),
        )

        document = self.store.document_for_candidate(current, candidate)

        self.assertEqual(document["unrelated_setting"], {"preserve": True})
        self.assertEqual(document["ai"], current["ai"])
        self.assertEqual(document["source_folder"], str(self.second))
        self.assertEqual(document["rules"], {"media": [".mp4"]})
        self.assertEqual(
            document["destination_folders"]["others"],
            str(self.organized / "others"),
        )

    def test_atomic_write_replaces_complete_document_without_temp_residue(self):
        self._write()
        document = self._document()
        document["archive_by_date"] = True

        self.store.write_document_atomic(document)

        self.assertEqual(json.loads(self.config_path.read_text("utf-8")), document)
        self.assertEqual(list(self.root.glob(".config.json.*.tmp")), [])

    def test_failed_replace_keeps_original_bytes_and_cleans_temp(self):
        self._write()
        original = self.config_path.read_bytes()

        with patch("app.product_configuration.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.write_document_atomic({"replacement": True})

        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".config.json.*.tmp")), [])

    def test_final_revision_check_preserves_edit_made_during_save(self):
        self._write()
        original = self._document()
        external = dict(original)
        external["external_edit"] = True
        expected_revision = configuration_revision(original)

        real_fsync = os.fsync
        fsync_calls = 0

        def fsync_and_edit(descriptor):
            nonlocal fsync_calls
            fsync_calls += 1
            real_fsync(descriptor)
            if fsync_calls == 1:
                self.config_path.write_text(
                    json.dumps(external),
                    encoding="utf-8",
                )

        with patch("app.product_configuration.os.fsync", side_effect=fsync_and_edit):
            with self.assertRaises(StaleConfigurationError):
                self.store.write_document_atomic(
                    {"candidate": True},
                    expected_revision=expected_revision,
                )

        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8")),
            external,
        )
        self.assertEqual(list(self.root.glob(".config.json.*.tmp")), [])

    def test_revision_is_canonical_for_equivalent_documents(self):
        first = {"a": 1, "b": {"x": 2}}
        second = {"b": {"x": 2}, "a": 1}

        self.assertEqual(configuration_revision(first), configuration_revision(second))

    def test_invalid_candidate_cannot_be_merged(self):
        with self.assertRaises(ProductConfigurationError):
            self.store.document_for_candidate(
                self._document(),
                self._candidate(rules=(ProductRule("bad/name", (".txt",)),)),
            )


if __name__ == "__main__":
    unittest.main()
