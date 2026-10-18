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
