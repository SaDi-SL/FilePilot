"""Selected-document preview; rendering is requested through the library worker."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from app.ui.qt.widgets.section_card import SectionCard


def read_library_preview(path, page=0):
    source = Path(path)
    if source.stat().st_size > 50 * 1024 * 1024:
        raise ValueError('Preview is limited to 50 MB. Use Open file for this document.')
    if source.suffix.lower() in {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}:
        from app.ui.qt.document_preview import render_preview
        image, page, count = render_preview(source, page)
        return {'path': str(source), 'image': image, 'page': page, 'count': count, 'text': ''}
    from app.content_reader import extract_file_content_result
    result = extract_file_content_result(source, max_chars=8000, lowercase=False)
    return {'path': str(source), 'image': None, 'page': 0, 'count': 1, 'text': result.text,
            'detail': result.detail or 'Use Open file to view this document in its own application.'}


class LibraryPreview(SectionCard):
    def __init__(self, controller, parent=None, *, request_key="preview"):
        super().__init__(parent, elevated=True)
        self.controller = controller
        self.request_key = request_key
        self.path = None
        self.page = 0
        self.count = 0
        self.image = None
        self.setMinimumWidth(240)
        self.setMinimumHeight(650)
        self.setProperty('cardAccent', 'purple')
        self.title = QLabel('Select a document')
        self.title.setProperty('role', 'sectionTitle')
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.status = QLabel('See its pages here, then open it or create a summary.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.canvas.setAccessibleName('Selected library document page preview')
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        self.scroll.setMinimumHeight(260)
        self.scroll.setMaximumHeight(420)
        self.scroll.hide()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName('Selected document text preview')
        self.text.setMinimumHeight(260)
        self.text.setMaximumHeight(420)
        self.text.hide()
        self.navigation = QWidget()
        row = QHBoxLayout(self.navigation)
        row.setContentsMargins(0, 0, 0, 0)
        self.previous = QPushButton('Previous page')
        self.next = QPushButton('Next page')
        self.previous.clicked.connect(lambda: self.load(self.page - 1))
        self.next.clicked.connect(lambda: self.load(self.page + 1))
        row.addWidget(self.previous)
        row.addWidget(self.next)
        self.navigation.hide()
        for widget in (self.title, self.status, self.scroll, self.text, self.navigation):
            self.content_layout.addWidget(widget)
        self._pending_page = 0
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(120)
        self._delay.timeout.connect(self._request)
        controller.result.connect(self._result)
        controller.error.connect(self._error)

    def select(self, path):
        path = Path(path) if path else None
        if path == self.path:
            return
        self._delay.stop()
        self.path = path
        self.image = None
        self.canvas.clear()
        self.scroll.hide()
        self.text.clear()
        self.text.hide()
        self.navigation.hide()
        self.title.setText(path.name if path else 'Select a document')
        self.status.setText('See its pages here, then open it or create a summary.')
        self.page = self.count = 0
        if path:
            self.load(0)

    def load(self, page):
        if self.path is None or page < 0 or self.count and page >= self.count:
            return
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.status.setText('Loading preview…')
        self._pending_page = page
        self._delay.start()

    def _request(self):
        if self.path is not None:
            self.controller.request(self.request_key, path=str(self.path), page=self._pending_page)

    def _result(self, key, result):
        if key != self.request_key or self.path is None or str(self.path) != result['path']:
            return
        self.image = result['image']
        self.page, self.count = result['page'], result['count']
        if self.image is not None:
            self.scroll.show()
            self.text.hide()
            self._scale()
            self.navigation.setVisible(self.count > 1)
            self.previous.setEnabled(self.page > 0)
            self.next.setEnabled(self.page + 1 < self.count)
            self.status.setText(f'Page {self.page + 1} of {self.count}' if self.path.suffix.lower() == '.pdf' else 'Image preview')
        else:
            self.text.setPlainText(result['text'])
            self.text.setVisible(bool(result['text']))
            self.status.setText('Text preview · first 8,000 characters' if result['text'] else result['detail'])

    def _error(self, key, message):
        if key == self.request_key and self.path:
            self.status.setText(message + ' Use Open file to check the original.')
            self.previous.setEnabled(self.page > 0)
            self.next.setEnabled(self.page + 1 < self.count)

    def _scale(self):
        if self.image is not None:
            width = max(160, self.scroll.viewport().width() - 12)
            self.canvas.setPixmap(QPixmap.fromImage(self.image).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale()
