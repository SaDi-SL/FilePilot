"""Export the editable SVG to a multi-resolution Windows icon (development only)."""
import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(ROOT / "assets/branding/filepilot-icon.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid brand SVG")
    image = QImage(512, 512, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    source = Image.frombytes("RGBA", (512, 512), bytes(image.constBits()),
                             "raw", "RGBA", image.bytesPerLine())
    source.save(ROOT / "assets/branding/filepilot-icon.png")
    sizes = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
    source.save(ROOT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes])
    app.quit()


if __name__ == "__main__":
    main()
