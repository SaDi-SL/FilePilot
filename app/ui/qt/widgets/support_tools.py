import json
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton
from app.ui.qt.widgets.section_card import SectionCard


class SupportTools(SectionCard):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.report = None
        self.release_url = ""
        heading = QLabel("Updates & support")
        heading.setProperty("role", "sectionTitle")
        self.content_layout.addWidget(heading)
        copy = QLabel("Check published releases when you choose. Updates open the release page; nothing is downloaded or installed automatically.")
        copy.setProperty("role", "secondary")
        copy.setWordWrap(True)
        self.content_layout.addWidget(copy)
        row = QVBoxLayout()
        self.check = QPushButton("Check for updates")
        self.check.clicked.connect(self._check)
        self.release = QPushButton("Open release page")
        self.release.setEnabled(False)
        self.release.clicked.connect(self._open)
        self.diagnostic = QPushButton("Prepare diagnostics")
        self.diagnostic.clicked.connect(self._diagnostic)
        for button in (self.check, self.release, self.diagnostic):
            row.addWidget(button)
        self.content_layout.addLayout(row)
        self.message = QLabel("Checks contact GitHub only when requested. Diagnostic reports stay on your computer.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.content_layout.addWidget(self.message)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(180)
        self.preview.hide()
        self.content_layout.addWidget(self.preview)
        self.export = QPushButton("Save diagnostic report")
        self.export.setEnabled(False)
        self.export.clicked.connect(self._save)
        self.content_layout.addWidget(self.export)
        controller.result.connect(self._result)
        controller.error.connect(self._error)

    def _check(self):
        self.check.setEnabled(False)
        self.release.setEnabled(False)
        self.message.setText("Checking the latest published release…")
        self.controller.request("updates")

    def _diagnostic(self):
        self.diagnostic.setEnabled(False)
        self.controller.request("diagnostics")

    def _result(self, key, value):
        if key == "updates":
            self.check.setEnabled(True)
            self.release_url = value.url
            self.release.setEnabled(bool(value.url))
            self.message.setText(value.message)
        elif key == "diagnostics":
            self.diagnostic.setEnabled(True)
            self.report = value
            self.preview.setPlainText(json.dumps(value, indent=2, ensure_ascii=False))
            self.preview.show()
            self.export.setEnabled(True)
            self.message.setText("Review this report before saving. It excludes document text, file names, paths and credentials.")

    def _error(self, key, message):
        if key in {"updates", "diagnostics"}:
            self.check.setEnabled(True)
            self.diagnostic.setEnabled(True)
            self.message.setText(message)

    def _open(self):
        if self.release_url:
            QDesktopServices.openUrl(QUrl(self.release_url))

    def _save(self):
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save local diagnostics", "FilePilot-diagnostics.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except OSError:
                self.message.setText("Could not save this report. Choose a writable location.")
            else:
                self.message.setText("Diagnostic report saved locally.")
