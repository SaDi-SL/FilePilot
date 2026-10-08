"""Bounded local previews, called only by background read tasks."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader, QImage, QPainter


def render_preview(path: Path, page: int = 0):
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Preview is limited to files up to 50 MB. Use Open for this file.")
    if path.suffix.lower() == ".pdf":
        from PySide6.QtPdf import QPdfDocument

        document = QPdfDocument()
        try:
            error = document.load(str(path))
            if error != QPdfDocument.Error.None_:
                raise ValueError("This PDF cannot be previewed. It may be damaged or password protected. Use Open.")
            count = document.pageCount()
            if not 0 <= page < count:
                raise ValueError("This PDF page is unavailable.")
            dimensions = document.pagePointSize(page).toSize()
            if dimensions.isEmpty():
                raise ValueError("This PDF page has invalid dimensions.")
            image = document.render(page, dimensions.scaled(QSize(1100, 1600), Qt.AspectRatioMode.KeepAspectRatio))
            if image.isNull():
                raise ValueError("Could not render this PDF page. Use Open.")
            # PDF pages can render with transparency; provide paper behind the text.
            paper = QImage(image.size(), QImage.Format.Format_RGB32)
            paper.fill(Qt.GlobalColor.white)
            painter = QPainter(paper)
            painter.drawImage(0, 0, image)
            painter.end()
            return paper, page, count
        finally:
            document.close()
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    dimensions = reader.size()
    if dimensions.isEmpty() or dimensions.width() * dimensions.height() > 40_000_000:
        raise ValueError("This image is unsupported or too large to preview. Use Open.")
    reader.setScaledSize(dimensions.scaled(QSize(1100, 1600), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("Could not read this image. Use Open.")
    return image, 0, 1
