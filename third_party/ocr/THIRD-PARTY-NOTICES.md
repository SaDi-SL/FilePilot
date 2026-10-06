# FilePilot OCR Third-Party Notices

FilePilot can bundle a local Tesseract OCR sidecar. The OCR sidecar is separate
third-party software and is not authored by FilePilot.

## Verified components

### Tesseract OCR
- Project: Tesseract OCR
- License: Apache License 2.0
- Runtime version for the currently reviewed Windows bundle: 5.5.3.20260724
- Upstream: https://github.com/tesseract-ocr/tesseract

The Tesseract documentation explicitly notes that Tesseract depends on other
packages which may use different open-source licenses.

### Leptonica
- Project: Leptonica
- License: BSD 2-Clause
- Runtime version reported by the reviewed Tesseract bundle: 1.87.0
- Upstream: https://github.com/DanBloomberg/leptonica

### Tesseract language data
- Repository: tessdata_fast
- Languages bundled by FilePilot: English (eng), Arabic (ara)
- License: Apache License 2.0
- Upstream: https://github.com/tesseract-ocr/tessdata_fast

## Windows runtime dependency audit

The reviewed Windows Tesseract distribution also contains dynamically linked
third-party libraries including image codecs, compression libraries, ICU,
GLib/Pango/Cairo, curl/archive dependencies, compiler runtimes, and related
libraries.

Their licenses are not covered merely by Tesseract's Apache-2.0 license.
FilePilot therefore treats each bundled runtime file as requiring explicit
license/provenance metadata in the OCR runtime manifest.

A public FilePilot release MUST NOT bundle the OCR runtime while any manifest
file entry is marked REVIEW_REQUIRED or otherwise lacks reviewed license
metadata. This notice is not a substitute for the individual license texts or
attribution notices required by those dependencies.

## Redistribution contract

The packaging pipeline verifies the hashes of all OCR runtime files and refuses
to build a bundled OCR installer when required provenance/license review is
incomplete. Exact license texts and required attribution notices must accompany
the final distributable OCR payload before public release.
