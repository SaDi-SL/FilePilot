# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


ROOT = Path(SPECPATH).resolve()
APP_NAME = "FilePilot"
VERSION_FILE = ROOT / "build" / "metadata" / "FilePilot-version-info.txt"

if not VERSION_FILE.is_file():
    raise SystemExit("Windows version metadata is missing; run python build.py")

a = Analysis(
    [str(ROOT / "app" / "packaged_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "config" / "default_config.json"), "config"),
        (str(ROOT / "icon.ico"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.Qt3DCore",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtLocation",
        "PySide6.QtMultimedia",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)


def keep_qt_binary(entry):
    destination = entry[0].replace("\\", "/").lower()
    plugin_marker = "pyside6/plugins/"
    if plugin_marker not in destination:
        return True
    plugin_path = destination.split(plugin_marker, 1)[1]
    return plugin_path in {
        "platforms/qwindows.dll",
        "imageformats/qico.dll",
    }


a.binaries = [entry for entry in a.binaries if keep_qt_binary(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    icon=str(ROOT / "icon.ico"),
    version=str(VERSION_FILE),
    uac_admin=False,
    uac_uiaccess=False,
)
