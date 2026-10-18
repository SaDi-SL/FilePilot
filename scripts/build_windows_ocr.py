"""Build a small, pinned OCR sidecar. Requires CMake and Visual Studio C++ tools.

Build-time downloads only; installed FilePilot never downloads OCR components.
The source lock records every archive/model hash. No external binary bundle is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OCR = ROOT / 'third_party/ocr'
LOCK = OCR / 'source-lock.json'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(item, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and sha256(target) == item['sha256']:
        return
    temporary = target.with_suffix(target.suffix + '.partial')
    try:
        with urllib.request.urlopen(item['url'], timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if sha256(temporary) != item['sha256']:
            raise RuntimeError(f"Checksum mismatch: {target.name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def run(*args):
    print('Running:', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, timeout=1200)


def build():
    if sys.platform != 'win32':
        raise RuntimeError('Build the Windows OCR sidecar on Windows with Visual Studio C++ and CMake')
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    work = ROOT / '.ocr-build'
    prefix = work / 'installed'
    prefix.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, item in lock['sources'].items():
        archive = work / 'downloads' / (name + '.tar.gz')
        download(item, archive)
        source = work / 'sources' / item['directory']
        if not source.is_dir():
            source.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive) as bundle:
                bundle.extractall(source.parent, filter='data')
        sources[name] = source

    # Hosted Windows images can move to a newer Visual Studio release.
    # Match an actually installed C++ toolchain to a supported CMake generator.
    vswhere = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installations = json.loads(subprocess.check_output([str(vswhere), '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-format', 'json'], text=True, encoding='utf-8'))
    help_text = subprocess.check_output(['cmake', '--help'], text=True, encoding='utf-8')
    import re
    available = {major: f'Visual Studio {major} {year}' for major, year in re.findall(r'Visual Studio (\d+) (\d{4})', help_text)}
    installation = next((item for item in installations if item['installationVersion'].split('.')[0] in available), None)
    if installation is None:
        raise RuntimeError('No supported Visual Studio C++ toolchain found')
    generator = available[installation['installationVersion'].split('.')[0]]
    common = ['-G', generator, '-A', 'x64',
              '-DCMAKE_GENERATOR_INSTANCE=' + installation['installationPath'],
              f'-DCMAKE_INSTALL_PREFIX={prefix}', f'-DCMAKE_PREFIX_PATH={prefix}',
              '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded', '-DBUILD_SHARED_LIBS=OFF',
              '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
              '-DZLIB_USE_STATIC_LIBS=ON']

    def compile_component(name, options):
        output = work / ('build-' + name)
        run('cmake', '-S', sources[name], '-B', output, *common, *options)
        run('cmake', '--build', output, '--config', 'Release', '--parallel', '4')
        run('cmake', '--install', output, '--config', 'Release')
        return output

    compile_component('zlib', ['-DZLIB_BUILD_SHARED=OFF', '-DZLIB_BUILD_STATIC=ON', '-DZLIB_BUILD_TESTING=OFF'])
    compile_component('libpng', ['-DPNG_SHARED=OFF', '-DPNG_STATIC=ON', '-DPNG_TESTS=OFF', '-DPNG_TOOLS=OFF'])
    compile_component('leptonica', ['-DSW_BUILD=OFF', '-DBUILD_PROG=OFF', '-DENABLE_PNG=ON', '-DENABLE_ZLIB=ON',
                                  *['-DENABLE_' + codec + '=OFF' for codec in ('GIF', 'JPEG', 'TIFF', 'WEBP', 'OPENJPEG')]])
    output = compile_component('tesseract', [f'-DLeptonica_DIR={prefix / "lib/cmake/leptonica"}',
                            '-DBUILD_TRAINING_TOOLS=OFF', '-DBUILD_TESTS=OFF',
                            '-DDISABLE_ARCHIVE=ON', '-DDISABLE_CURL=ON', '-DDISABLE_TIFF=ON',
                            '-DGRAPHICS_DISABLED=ON', '-DDISABLED_LEGACY_ENGINE=ON',
                            '-DOPENMP_BUILD=OFF', '-DENABLE_NATIVE=OFF', '-DWIN32_MT_BUILD=ON',
                            '-DINSTALL_CONFIGS=OFF'])
    runtime = OCR / 'runtime'
    if runtime.exists():
        shutil.rmtree(runtime)
    (runtime / 'tessdata').mkdir(parents=True)
    shutil.copy2(prefix / 'bin/tesseract.exe', runtime / 'tesseract.exe')
    license_expression = 'Apache-2.0 AND BSD-2-Clause AND Libpng AND Zlib'
    files = [{'path': 'tesseract.exe', 'sha256': sha256(runtime / 'tesseract.exe'),
              'license': license_expression, 'component': 'Tesseract 5.5.3 / Leptonica 1.87.0 / libpng 1.6.59 / zlib 1.3.2 (static)',
              'source': lock['sources']['tesseract']['url']}]
    for language, item in lock['models'].items():
        target = runtime / 'tessdata' / (language + '.traineddata')
        download(item, target)
        files.append({'path': target.relative_to(runtime).as_posix(), 'sha256': item['sha256'],
                      'license': 'Apache-2.0', 'component': 'tessdata_fast ' + language, 'source': item['url']})
    provenance = runtime / 'source-lock.json'
    shutil.copy2(LOCK, provenance)
    files.append({'path': provenance.name, 'sha256': sha256(provenance), 'license': license_expression,
                  'component': 'Exact OCR source and language provenance', 'source': lock['sources']['tesseract']['url']})
    manifest = {'schema_version': 1, 'bundled': True,
                'engine': {'name': 'Tesseract OCR', 'version': '5.5.3', 'license': license_expression,
                           'distribution_note': 'Static MSVC build. PNG input; FilePilot converts other images to temporary PNG.'},
                'languages': [{'code': code, 'source_family': 'tessdata_fast', 'license': 'Apache-2.0'} for code in lock['models']],
                'files': files}
    (OCR / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    run(runtime / 'tesseract.exe', '--version')
    run(runtime / 'tesseract.exe', '--list-langs', '--tessdata-dir', runtime / 'tessdata')
    print('Pinned English, Arabic and Swedish OCR sidecar ready.', flush=True)


if __name__ == '__main__':
    build()
