# Windows release delivery

Earlier Windows packaging, installation, launch and uninstallation were successfully tested on the user's machine. New code changes still need packaging validation against their own source fingerprint; an old working installer is not evidence that it contains new features.

## Build integrity

```powershell
python build.py --dry-run
python build.py
```

When Inno Setup completes, `dist/release-manifest.json` lists version, source fingerprint, signing-request flag and SHA-256 hashes for the executable and installer. Keep the manifest with distributed binaries. These checksums detect changed artifacts; they do not establish publisher trust on their own.

## Optional publisher signing

Use an existing code-signing certificate with private-key access in the current user's Windows **My** certificate store and the Windows SDK's SignTool. Set `FILEPILOT_SIGNTOOL` to its executable path and `FILEPILOT_CERT_THUMBPRINT` to the certificate's SHA-1 identifier (the file digest remains SHA-256).

```powershell
python build.py --signed
```

The executable is signed and verified before its build identity hash is recorded. A signed installer build verifies the application signature first, signs the generated installer and verifies that signature. A missing tool/certificate or failed signature verification fails the signed build; it never silently falls back to unsigned output. No private certificate or password is packaged or written into diagnostic reports.

`python build.py --signed --installer` requires an already signed executable matching the current source fingerprint. Timestamping uses the configured implementation's RFC 3161 endpoint. SignTool reference: https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool

No publisher certificate was available in the development environment, so an actual signed Windows release has **not** been produced or verified here.

## Updates and persistence

The in-app update check is explicit and read only. It compares a validated stable GitHub release version against the current build and opens the release page. It never claims a development build is identical to a published stable build. Installer upgrades retain the existing stable AppId and keep binaries separate from user data. Do not remove or overwrite `%LOCALAPPDATA%\FilePilot` when replacing the application.

## Release gate

Before publishing a stable release, resolve the known validation blockers, run the full Windows test workflow, build from the intended commit, verify OCR runtime completeness, exercise the new library/AI controls in that package, verify artifact hashes, and apply the publisher signature when a certificate is available. Do not label this development branch as a finished stable release.

## GitHub candidate delivery

`.github/workflows/windows-build.yml` builds a candidate on Windows 2025 after all unit modules pass. It requires an installer to be produced, then runs `scripts/windows_package_smoke.py` on a GitHub-hosted disposable runner. The smoke check verifies manifest hashes, installs into a temporary folder/profile, sees the actual Qt main window through the native Windows platform plugin, closes setup dialogs and the application normally, uninstalls, and checks that external user configuration and a preservation marker survive. It refuses to run on ordinary PCs or self-hosted runners because installer registry state must be isolated.

Only a passing package check uploads the executable, installer, SHA-256 manifest, package-validation report and build notes as a 14-day artifact. Validation logs are uploaded separately even on failure. The workflow does not publish a release, sign binaries, install Ollama/models, or claim bundled OCR when the runtime manifest is disabled. Existing user-performed installation tests remain valid for their earlier build; this check supplies evidence tied to the new commit.

### Verified candidate

The candidate from `24d769293d3f9f1cbd43d37c91895de393f0d94f` passed all 820 Windows tests and the installed-package check described above. Run: https://github.com/SaDi-SL/FilePilot/actions/runs/39135436281. Its artifact contains both the installer and executable, checksums, package-validation JSON and build notes. Retention ends 1 November 2026. See `docs/release-validation.md` for the evidence and remaining release limits.

### OCR candidate build

The candidate workflow now runs `python scripts/build_windows_ocr.py` before
PyInstaller. It compiles pinned Tesseract/Leptonica/libpng/zlib sources with the
installed Visual Studio C++ toolchain and statically links them. All source
archives and English/Arabic/Swedish `tessdata_fast` models are checked against
`third_party/ocr/source-lock.json`. Downloads happen during build, never on the
user's PC. The generated runtime manifest records the exact compiled executable
hash and files; the existing staging gate verifies them before packaging.

For a local Windows source build with OCR, install Visual Studio C++ build tools
and CMake, install `requirements-build.txt`, run the OCR build script, then run
`python build.py`. Generated files live in `.ocr-build/` and
`third_party/ocr/runtime/`, outside the `build/` folder cleared by PyInstaller.
Run a normal unbundled source build without that extra step if OCR is not needed.
The recommended end-user route is the tested Setup artifact, requiring no Python,
CMake, compiler, separate Tesseract install or OCR model downloads.

Installed-package validation calls the actual installed FilePilot executable's
internal `--package-ocr-check` operation. It checks recognized English, Arabic and
Swedish text, JPEG/TIFF/WebP/BMP conversion and an image-only PDF, verifies that
all three bundled languages are used, and hashes every source before/after. The
operation writes a new report exclusively and never overwrites a document or an
existing report. It does not invoke Ollama. Normal launch remains the Qt app.

The OCR-enabled candidate from `11783c8016c63759137185bd625ba2fe0a672d45`
passed all 825 Windows tests and all eight installed OCR checks, including exact
Arabic words and Swedish diacritics, plus the native application/uninstall check.
Download: https://github.com/SaDi-SL/FilePilot/actions/runs/39138296815/artifacts/12140314653.
Evidence and limitations are recorded in `docs/release-validation.md`. This
supersedes the earlier OCR-disabled candidate and remains unsigned/development.
