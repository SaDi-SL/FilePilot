# FilePilot bundled OCR notices

Windows candidate builds compile Tesseract 5.5.3, Leptonica 1.87.0,
libpng 1.6.59 and zlib 1.3.2 from the source archives recorded with SHA-256
in source-lock.json. English, Arabic and Swedish models use the pinned
`tessdata_fast` revision recorded in the same file. Build-time downloads are
verified before use. The generated runtime manifest records the compiled
executable checksum and the exact files included in this installer.

The executable statically links only the listed components and the Microsoft
C/C++ runtime. It does not include the old MSYS2 binary dependency bundle
catalogued by license-map.json. That catalogue is for the optional manual
import tool only; it is not a list of files distributed in this candidate.

Redistribution texts with the upstream copyright notices accompany the runtime:

- Tesseract and language data: Apache-2.0; Tesseract AUTHORS included.
- Leptonica: BSD-2-Clause, Copyright (C) 2001-2020 Leptonica.
- libpng: PNG Reference Library License, copyright notices in Libpng.txt.
- zlib: zlib license, copyright notices in Zlib.txt.

The license-bundle.json file maps each identifier to its accompanying text.
FilePilot converts supported non-PNG images to temporary PNGs; original files
are retained. PDF rendering uses the application's separately packaged pypdfium2.
