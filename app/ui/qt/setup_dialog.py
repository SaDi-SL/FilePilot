from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
                              QPushButton, QStackedWidget, QVBoxLayout, QWidget, QPlainTextEdit)

from app.application_service import StartupStatus
from app.local_ai_readiness import check_local_ai
from app.product_configuration import ConfigurationDataState
from app.ui.qt.pages.folders import FoldersPage
from app.ui.qt.pages.settings import SettingsPage
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


class _ProbeSignals(QObject):
    finished = Signal(object)


class _ProbeTask(QRunnable):
    def __init__(self, generation, model):
        super().__init__()
        self.generation = generation
        self.model = model
        self.signals = _ProbeSignals()

    def run(self):
        self.signals.finished.emit((self.generation, check_local_ai(self.model)))


class SetupDialog(QDialog):
    """Guided setup using the same validated configuration editors as the workspace."""
    index_requested = Signal()

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._probe_generation = 0
        self._probe_running = False
        self._result = None
        self._saved_model = None
        self.setWindowTitle("Set up FilePilot")
        self.setAccessibleName("FilePilot guided setup")
        self.setModal(True)
        self.resize(980, 760)
        self.setMinimumSize(680, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        self.heading = QLabel("Make FilePilot yours")
        self.heading.setProperty("role", "pageTitle")
        self.progress = QLabel()
        self.progress.setProperty("role", "eyebrow")
        root.addWidget(self.heading)
        root.addWidget(self.progress)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.folders = FoldersPage(bridge)
        self.folders.CONTEXT = "setup-folders"
        self._compact_editor(self.folders)
        self.stack.addWidget(self.folders)

        ai_panel = QWidget()
        ai_layout = QVBoxLayout(ai_panel)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        card = SectionCard()
        card.setProperty("cardAccent", "cool")
        heading = QLabel("Local AI · optional")
        heading.setProperty("role", "sectionTitle")
        self.probe_status = QLabel("Check whether Ollama and the models used for local answers are installed.")
        self.probe_status.setWordWrap(True)
        self.probe_status.setTextFormat(Qt.TextFormat.PlainText)
        self.probe_status.setProperty("role", "secondary")
        self.probe_status.setAccessibleName("Local AI readiness result")
        self.check_button = QPushButton("Check local AI")
        self.check_button.setProperty("variant", "primary")
        self.check_button.clicked.connect(self._check_ai)
        inventory_row = QHBoxLayout()
        self.models = QComboBox()
        self.models.setAccessibleName("Installed Ollama models")
        self.models.setMinimumWidth(150)
        self.models.addItem("Check to see installed models")
        self.models.setEnabled(False)
        self.use_model = QPushButton("Use selected model")
        self.use_model.setEnabled(False)
        self.use_model.clicked.connect(self._use_model)
        inventory_row.addWidget(self.models, 1)
        inventory_row.addWidget(self.use_model)
        inventory_row.addWidget(self.check_button)
        card.content_layout.addWidget(heading)
        card.content_layout.addWidget(self.probe_status)
        card.content_layout.addLayout(inventory_row)
        ai_layout.addWidget(card)
        self.settings = SettingsPage(bridge)
        self.settings.CONTEXT = "setup-settings"
        self.settings.settings_tabs.setCurrentIndex(1)
        self.settings.settings_tabs.tabBar().hide()
        self._compact_editor(self.settings)
        ai_layout.addWidget(self.settings, 1)
        self.stack.addWidget(ai_panel)

        finish = QWidget()
        finish_layout = QVBoxLayout(finish)
        finish_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        summary_card = SectionCard(elevated=True)
        summary_card.setProperty("cardAccent", "purple")
        finish_heading = QLabel("Your workspace")
        finish_heading.setProperty("role", "headline")
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName("Saved workspace summary")
        self.summary.setMinimumHeight(230)
        self.summary.setMaximumHeight(320)
        summary_card.content_layout.addWidget(finish_heading)
        summary_card.content_layout.addWidget(self.summary)
        next_copy = QLabel("Next: browse your files, or update the search index to prepare local answers. Start automatic organization from Overview when you are ready.")
        next_copy.setWordWrap(True)
        next_copy.setProperty("role", "secondary")
        summary_card.content_layout.addWidget(next_copy)
        finish_layout.addWidget(summary_card)
        finish_layout.addStretch(1)
        self.stack.addWidget(finish)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("role", "secondary")
        root.addWidget(self.notice)
        actions = QHBoxLayout()
        self.later_button = QPushButton("Set up later")
        self.later_button.clicked.connect(self.reject)
        actions.addWidget(self.later_button)
        actions.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        actions.addWidget(self.back_button)
        self.index_button = QPushButton("Go to search indexing")
        self.index_button.clicked.connect(self._go_to_index)
        actions.addWidget(self.index_button)
        self.next_button = QPushButton("Continue")
        self.next_button.setProperty("variant", "primary")
        self.next_button.clicked.connect(self._next)
        actions.addWidget(self.next_button)
        root.addLayout(actions)
        for signal_name in ("configuration_snapshot_changed", "settings_snapshot_changed", "state_changed",
                            "configuration_save_started", "configuration_save_completed",
                            "settings_save_started", "settings_save_completed"):
            signal = getattr(bridge, signal_name, None)
            if signal is not None:
                signal.connect(self._refresh)
        self.settings.ollama_model.textChanged.connect(self._refresh)
        self.settings.ai_enabled.toggled.connect(self._refresh)
        self.settings.ai_provider.currentIndexChanged.connect(self._refresh)
        self.folders.destination_input.textChanged.connect(self._refresh)
        self.folders.watch_tree.itemChanged.connect(self._refresh)
        self._refresh()

    @staticmethod
    def _compact_editor(editor):
        editor.setProperty("guidedSetup", True)
        for index in range(3):
            editor.page_layout.itemAt(index).widget().hide()

    def _folders_valid(self):
        snapshot = getattr(self.bridge, "configuration_snapshot", None)
        return bool(snapshot and snapshot.state is ConfigurationDataState.AVAILABLE
                    and snapshot.folders and snapshot.folders.topology_valid
                    and snapshot.folders.organized_exists and not snapshot.issues
                    and any(f.active and f.exists for f in snapshot.folders.watch_folders))

    def _clean(self):
        return not (self.folders._dirty or self.settings._dirty or
                    self.folders._saving or self.settings._saving)

    @Slot()
    def _refresh(self, *_args):
        model = self.settings._baseline.ai.ollama_model if self.settings._baseline.ai else None
        if model != self._saved_model:
            self._saved_model = model
            self._probe_generation += 1
            self._probe_running = False
            self._result = None
            self.models.setEnabled(False)
            self.probe_status.setText("Check local AI to verify the saved model." if model else "Load AI settings before checking local models.")
        step = self.stack.currentIndex()
        self.progress.setText(("STEP 1 OF 3 · CHOOSE YOUR FOLDERS", "STEP 2 OF 3 · CHECK LOCAL AI", "STEP 3 OF 3 · REVIEW & FINISH")[step])
        self.back_button.setVisible(step > 0)
        self.index_button.setVisible(step == 2)
        self.next_button.setText("Finish" if step == 2 else "Continue")
        self.check_button.setEnabled(bool(model) and not self._probe_running and not self.settings._dirty and not self.settings._saving)
        self.use_model.setEnabled(bool(self._result and self._result.models) and not self.settings._saving)
        self.next_button.setEnabled(not self.folders._saving and not self.settings._saving)
        self.later_button.setEnabled(not self.folders._saving and not self.settings._saving)
        self.back_button.setEnabled(not self.folders._saving and not self.settings._saving)
        self.index_button.setEnabled(self._folders_valid() and self._clean())
        if step == 2:
            self._render_summary()

    def _next(self):
        if not self._clean():
            self.notice.setText("Save or revert your changes before continuing.")
            return
        if not self._folders_valid():
            self.notice.setText("Choose valid watch folders and a destination, then save them. Review any folder or rule validation errors before continuing.")
            self.stack.setCurrentIndex(0)
            self._refresh()
            return
        if self.stack.currentIndex() == 2:
            self.accept()
            return
        self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
        self.notice.setText("Local AI is optional. You can continue and configure it later." if self.stack.currentIndex() == 1 else "")
        self._refresh()

    def _back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self.notice.clear()
        self._refresh()

    def _check_ai(self):
        if self.settings._dirty:
            self.notice.setText("Save your model choice before checking it.")
            return
        if not self._saved_model or self._probe_running:
            return
        self._probe_generation += 1
        self._probe_running = True
        self._result = None
        self.probe_status.setText("Checking local Ollama…")
        self._refresh()
        task = _ProbeTask(self._probe_generation, self._saved_model)
        task.signals.finished.connect(self._probe_finished)
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _probe_finished(self, payload):
        generation, result = payload
        if generation != self._probe_generation:
            return
        self._probe_running = False
        self._result = result
        self.models.clear()
        self.models.addItems(result.models or ("No model inventory available",))
        self.models.setEnabled(bool(result.models))
        selected = self.models.findText(result.answer_model)
        if selected >= 0:
            self.models.setCurrentIndex(selected)
        if result.installed:
            text = f"Ollama found · Answer model: {result.answer_model} · Search model: {result.embedding_model}. Both are installed; indexing and a first answer will verify runtime use."
        elif result.reachable:
            missing = []
            if not result.answer_installed:
                missing.append(f"answer model {result.answer_model}")
            if not result.embedding_installed:
                missing.append(f"search model {result.embedding_model}")
            text = "Ollama found. Missing: " + ", ".join(missing) + ". Install these models in Ollama, or continue without local AI."
        else:
            text = result.error + " You can continue without local AI."
        self.probe_status.setText(text)
        self._refresh()

    def _use_model(self):
        if self._result and self.models.currentText() in self._result.models:
            self.settings.ollama_model.setText(self.models.currentText())
            self.notice.setText("Save settings to apply this model. Choose a chat model for answers, not an embedding model.")

    def _render_summary(self):
        snapshot = getattr(self.bridge, "configuration_snapshot", None)
        lines = []
        if snapshot and snapshot.folders:
            active = [str(f.path) for f in snapshot.folders.watch_folders if f.active]
            lines.append("Watch folders:\n" + "\n".join(active))
            lines.append("Organized destination:\n" + str(snapshot.folders.organized_folder))
        lines.append("Local AI: " + ("models installed" if self._result and self._result.installed else "not verified; optional"))
        state = self.bridge.snapshot
        lines.append("Monitoring: " + state.monitor_state.value)
        if state.startup_status is StartupStatus.BLOCKED:
            lines.append("Automatic organization is blocked. Review the recovery or status message in Overview.")
        self.summary.setPlainText("\n\n".join(lines))

    def _go_to_index(self):
        if self._folders_valid() and self._clean():
            self.accept()
            self.index_requested.emit()

    def accept(self):
        self._probe_generation += 1
        super().accept()

    def reject(self):
        if self.folders._saving or self.settings._saving:
            self.notice.setText("Wait for the save to finish before closing setup.")
            return
        if not self._clean():
            response = QMessageBox.question(self, "Unsaved setup changes", "Discard unsaved changes and close setup?",
                                            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                            QMessageBox.StandardButton.Cancel)
            if response != QMessageBox.StandardButton.Discard:
                return
        self._probe_generation += 1
        super().reject()
