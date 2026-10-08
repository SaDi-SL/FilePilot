from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QTextOption, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget, QScrollArea,
)

from app.file_browser import browse_directory, format_size
from app.ui.qt.icons import folder_art, navigation_icon
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.document_preview import render_preview


class _Signals(QObject):
    finished = Signal(object)


class _ReadTask(QRunnable):
    def __init__(self, generation, action):
        super().__init__()
        self.generation = generation
        self.action = action
        self.signals = _Signals()

    def run(self):
        try:
            result = (self.generation, self.action(), None)
        except Exception as error:
            result = (self.generation, None, str(error))
        self.signals.finished.emit(result)


class FileBrowser(QWidget):
    """Browse the configured destination without modifying any files."""
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.root = self.current_path = None
        self._generation = self._preview_generation = 0
        self._entries = ()
        self._partial = False
        self._visual_path = None
        self._visual_page = 0
        self._visual_count = 0
        self._rendered_image = None
        self._pool = QThreadPool.globalInstance()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        toolbar = QHBoxLayout()
        self.home_button = QPushButton("Organized folder")
        self.home_button.clicked.connect(lambda: self.load(self.root))
        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(self._up)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.load(self.current_path))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter this folder…")
        self.filter_input.setAccessibleName("Filter files and folders by name")
        self.filter_input.textChanged.connect(self._render)
        self.sort_input = QComboBox()
        self.sort_input.addItems(("Name", "Newest first", "Largest first"))
        self.sort_input.setAccessibleName("Sort files and folders")
        self.sort_input.currentIndexChanged.connect(self._render)
        for widget in (self.home_button, self.up_button, self.refresh_button):
            toolbar.addWidget(widget)
        toolbar.addWidget(self.filter_input, 1)
        toolbar.addWidget(self.sort_input)
        layout.addLayout(toolbar)
        self.location = QLabel("Waiting for the organized folder settings…")
        self.location.setWordWrap(True)
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.location)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)
        body = QHBoxLayout()
        self.items = QListWidget()
        self.items.setAccessibleName("Organized files and folders")
        self.items.setViewMode(QListView.ViewMode.IconMode)
        self.items.setResizeMode(QListView.ResizeMode.Adjust)
        self.items.setMovement(QListView.Movement.Static)
        self.items.setIconSize(QSize(64, 64))
        self.items.setGridSize(QSize(210, 174))
        self.items.setSpacing(8)
        self.items.setWordWrap(True)
        self.items.setMinimumHeight(380)
        self.items.setProperty("fileGrid", True)
        self.items.currentItemChanged.connect(self._select)
        self.items.itemActivated.connect(self._activate)
        body.addWidget(self.items, 1)
        self.details = SectionCard(elevated=True)
        self.details.setFixedWidth(260)
        self.details.setProperty("cardAccent", "purple")
        self.detail_title = QLabel("Select a file or folder")
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setWordWrap(True)
        self.detail_title.setProperty("role", "sectionTitle")
        self.detail_info = QPlainTextEdit()
        self.detail_info.setReadOnly(True)
        self.detail_info.setAccessibleName("Selected file full path and details")
        self.detail_info.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.detail_info.setFixedHeight(170)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Selected text file preview")
        self.preview.setMinimumHeight(120)
        self.preview.hide()
        self.visual_panel = QWidget()
        visual_layout = QVBoxLayout(self.visual_panel)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        self.visual_status = QLabel()
        self.visual_status.setWordWrap(True)
        self.visual_status.setTextFormat(Qt.TextFormat.PlainText)
        visual_layout.addWidget(self.visual_status)
        self.visual_zoom = QComboBox()
        self.visual_zoom.addItems(("Fit width", "100%", "150%"))
        self.visual_zoom.setAccessibleName("Preview zoom")
        self.visual_zoom.currentIndexChanged.connect(self._scale_visual)
        visual_layout.addWidget(self.visual_zoom)
        self.visual_scroll = QScrollArea()
        self.visual_scroll.setWidgetResizable(True)
        self.visual_scroll.setFixedHeight(280)
        self.visual_image = QLabel()
        self.visual_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.visual_image.setAccessibleName("Selected PDF page or image preview")
        self.visual_scroll.setWidget(self.visual_image)
        visual_layout.addWidget(self.visual_scroll)
        self.visual_previous = QPushButton("Previous")
        self.visual_next = QPushButton("Next")
        self.visual_previous.clicked.connect(lambda: self._load_visual(self._visual_page - 1))
        self.visual_next.clicked.connect(lambda: self._load_visual(self._visual_page + 1))
        self.visual_navigation = QWidget()
        nav = QHBoxLayout(self.visual_navigation)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.addWidget(self.visual_previous)
        nav.addWidget(self.visual_next)
        visual_layout.addWidget(self.visual_navigation)
        self.visual_panel.hide()
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("variant", "primary")
        self.open_button.clicked.connect(lambda: self._activate(self.items.currentItem()))
        self.open_folder_button = QPushButton("Open containing folder")
        self.open_folder_button.clicked.connect(self._open_parent)
        for widget in (self.detail_title, self.detail_info, self.preview, self.visual_panel, self.open_button, self.open_folder_button):
            self.details.content_layout.addWidget(widget)
        self.details.content_layout.addStretch(1)
        body.addWidget(self.details)
        layout.addLayout(body)
        self._select(None)
        self._set_controls(False)
        signal = getattr(bridge, "configuration_snapshot_changed", None)
        if signal is not None:
            signal.connect(self.configure)
        snapshot = getattr(bridge, "configuration_snapshot", None)
        if snapshot is not None:
            self.configure(snapshot)

    def configure(self, snapshot):
        folders = getattr(snapshot, "folders", None)
        if folders is None:
            self._generation += 1
            self.root = self.current_path = None
            self._entries = ()
            self.items.clear()
            self.location.setText("Organized folder is unavailable.")
            self._set_controls(False)
            self.status.setText("Choose an organized destination in Folders to browse your files.")
            return
        root = Path(folders.organized_folder).resolve()
        if root != self.root:
            self.root = root
            self.load(root)

    def _set_controls(self, enabled):
        for widget in (self.home_button, self.up_button, self.refresh_button):
            widget.setEnabled(enabled)
        self.up_button.setEnabled(enabled and self.current_path != self.root)

    def load(self, path):
        if self.root is None or path is None:
            return
        self._generation += 1
        self.filter_input.clear()
        self.items.clear()
        self._entries = ()
        self._partial = False
        self.current_path = Path(path)
        self.location.setText(str(path))
        self.status.setText("Reading files and folders…")
        self._set_controls(False)
        root, directory = self.root, Path(path)
        task = _ReadTask(self._generation, lambda: browse_directory(root, directory))
        task.signals.finished.connect(self._loaded)
        self._pool.start(task)

    @Slot(object)
    def _loaded(self, result):
        generation, listing, error = result
        if generation != self._generation:
            return
        self._set_controls(True)
        if error:
            self.status.setText("Could not read this folder. It may be missing or inaccessible. " + error)
            return
        self.current_path = listing.path
        self._entries = listing.entries
        self._partial = listing.partial
        self._render()

    def _render(self, *args):
        self.items.clear()
        query = self.filter_input.text().casefold()
        entries = [entry for entry in self._entries if query in entry.path.name.casefold()]
        key = (lambda e: e.path.name.casefold())
        if self.sort_input.currentIndex() == 1:
            key = lambda e: -e.modified
        elif self.sort_input.currentIndex() == 2:
            key = lambda e: -e.size
        entries.sort(key=lambda e: (not e.is_directory, key(e), e.path.name.casefold()))
        for entry in entries:
            name = entry.path.name
            short_name = name if len(name) <= 48 else name[:45] + "…"
            if entry.is_directory:
                files_label = "file" if entry.file_count == 1 else "files"
                folders_label = "folder" if entry.folder_count == 1 else "folders"
                count = f"{entry.file_count} {files_label} · {entry.folder_count} {folders_label}"
                summary = ("At least " if entry.partial else "") + count
                size = format_size(entry.size) + " direct files"
                icon = QIcon(folder_art(64))
            else:
                summary = entry.path.suffix.upper().lstrip(".") or "File"
                size = format_size(entry.size)
                icon = navigation_icon("brand")
            item = QListWidgetItem(icon, f"{short_name}\n{summary}\n{size}")
            item.setSizeHint(QSize(194, 158))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(str(entry.path))
            self.items.addItem(item)
        self.status.setText(
            f"{len(entries)} items shown" + (" · Some entries omitted due to read errors or the 1,000-item limit." if getattr(self, "_partial", False) else "")
            + " · Folder counts and sizes cover direct children only."
            if entries else "No matching files or folders." if query else "No readable entries found." if getattr(self, "_partial", False) else "This folder is empty."
        )

    def _select(self, item, previous=None):
        self._preview_generation += 1
        self.preview.clear()
        self.preview.hide()
        self._visual_path = None
        self._visual_count = 0
        self._rendered_image = None
        self.details.setFixedWidth(260)
        self.visual_image.clear()
        self.visual_image.setMinimumSize(0, 0)
        self.visual_panel.hide()
        self.open_button.setEnabled(item is not None)
        self.open_folder_button.setEnabled(item is not None)
        if item is None:
            self.detail_title.setText("Select a file or folder")
            self.detail_title.setToolTip("")
            self.detail_info.setPlainText("See its details here. Double-click a folder to browse inside.")
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.detail_title.setText(self.detail_title.fontMetrics().elidedText(
            entry.path.name, Qt.TextElideMode.ElideMiddle, self.details.width() - 32
        ))
        self.detail_title.setToolTip(entry.path.name)
        date = datetime.fromtimestamp(entry.modified).strftime("%Y-%m-%d %H:%M")
        self.detail_info.setPlainText(f"{entry.path}\n\nModified: {date}\nSize: {format_size(entry.size)}" + (" (direct files; partial)" if entry.partial else " (direct files)" if entry.is_directory else ""))
        if not entry.is_directory and entry.path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}:
            self._visual_path = entry.path
            self.details.setFixedWidth(360)
            self.visual_zoom.setCurrentIndex(0)
            self._load_visual(0)
        if not entry.is_directory and entry.path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".log", ".py"}:
            path = entry.path
            def read_text():
                with path.open("rb") as stream:
                    return stream.read(16384).decode("utf-8", errors="replace")
            task = _ReadTask(self._preview_generation, read_text)
            task.signals.finished.connect(self._preview_loaded)
            self._pool.start(task)

    @Slot(object)
    def _preview_loaded(self, result):
        generation, text, error = result
        if generation != self._preview_generation:
            return
        self.preview.setPlainText("Could not read the text preview." if error else text + "\n\n[Preview: up to the first 16 KB]")
        self.preview.show()

    def _load_visual(self, page):
        if self._visual_path is None or page < 0:
            return
        if self._visual_count and page >= self._visual_count:
            return
        self._preview_generation += 1
        self.visual_panel.show()
        self._rendered_image = None
        self.visual_image.clear()
        self.visual_image.setMinimumSize(0, 0)
        self.visual_zoom.setEnabled(False)
        self.visual_status.setText("Loading preview…")
        self.visual_previous.setEnabled(False)
        self.visual_next.setEnabled(False)
        path = self._visual_path
        self.visual_navigation.setVisible(path.suffix.lower() == ".pdf")
        task = _ReadTask(self._preview_generation, lambda: render_preview(path, page))
        task.signals.finished.connect(self._visual_loaded)
        self._pool.start(task)

    @Slot(object)
    def _visual_loaded(self, result):
        generation, rendered, error = result
        if generation != self._preview_generation:
            return
        self.visual_zoom.setEnabled(True)
        if error:
            self.visual_status.setText(error)
            return
        image, page, count = rendered
        self._visual_page, self._visual_count = page, count
        self._rendered_image = image
        self._scale_visual()
        self.visual_status.setText(f"Page {page + 1} of {count} · Scroll to read" if self._visual_path.suffix.lower() == ".pdf" else "Image preview · Scroll to view")
        self.visual_previous.setEnabled(page > 0)
        self.visual_next.setEnabled(page + 1 < count)

    def _scale_visual(self, *args):
        if self._rendered_image is None:
            return
        image = self._rendered_image
        width = self.details.width() - 56 if self.visual_zoom.currentIndex() == 0 else int(image.width() * (1 if self.visual_zoom.currentIndex() == 1 else 1.5))
        pixmap = QPixmap.fromImage(image).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.visual_image.setPixmap(pixmap)
        self.visual_image.setMinimumSize(pixmap.size())

    def _activate(self, item):
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry.is_directory:
            self.load(entry.path)
        else:
            self._open_external(entry.path)

    def _open_parent(self):
        item = self.items.currentItem()
        if item:
            self._open_external(item.data(Qt.ItemDataRole.UserRole).path.parent)

    def _open_external(self, path):
        if not path.exists():
            self.status.setText("This location is no longer available. Refresh the folder.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.setText("Could not open this location in the default application.")

    def _up(self):
        if self.current_path and self.current_path != self.root:
            self.load(self.current_path.parent)
