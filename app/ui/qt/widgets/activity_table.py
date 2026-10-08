from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from app.application_service import ActivityRecord
from app.ui.qt.activity_presentation import activity_status_presentation
from app.ui.qt.icons import status_icon


class ActivityTable(QTreeWidget):
    TIME_COLUMN = 0
    FILE_COLUMN = 1
    CATEGORY_COLUMN = 2
    RESULT_COLUMN = 3
    DESTINATION_COLUMN = 4

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._compact = compact
        self.setProperty("activityTable", True)
        self.setAccessibleName(
            "Recent FilePilot activity" if compact else "FilePilot activity"
        )
        self.setColumnCount(5)
        self.setHeaderLabels(("Time", "File", "Category", "Result", "Destination"))
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.header().setHighlightSections(False)
        self.header().setStretchLastSection(False)
        for column in range(self.columnCount()):
            self.header().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Fixed,
            )
        if compact:
            self.setMinimumHeight(190)
            self.setMaximumHeight(206)

    def set_records(self, records: Iterable[ActivityRecord]) -> None:
        selected_id = self.selected_operation_id()
        self.clear()
        selected_item = None
        for record in records:
            presentation = activity_status_presentation(record.status)
            result_label = (
                presentation.label
                if record.durable
                else f"{presentation.label} (Live)"
            )
            destination = (
                str(record.display_destination)
                if record.display_destination is not None
                else "Unavailable"
            )
            timestamp = (
                record.occurred_at_utc.astimezone().strftime("%b %d, %H:%M")
                if record.occurred_at_utc is not None
                else "Unknown"
            )
            item = QTreeWidgetItem(
                (
                    timestamp,
                    record.filename,
                    record.category or "Unknown",
                    result_label,
                    destination,
                )
            )
            item.setIcon(self.RESULT_COLUMN, status_icon(presentation.color))
            item.setData(
                self.TIME_COLUMN,
                Qt.ItemDataRole.UserRole,
                record.operation_id,
            )
            item.setData(
                self.TIME_COLUMN,
                Qt.ItemDataRole.UserRole + 1,
                record,
            )
            item.setToolTip(self.TIME_COLUMN, timestamp)
            item.setToolTip(self.FILE_COLUMN, str(record.source_path))
            item.setToolTip(self.CATEGORY_COLUMN, record.category or "Category unavailable")
            item.setToolTip(self.RESULT_COLUMN, self._result_tooltip(record))
            item.setToolTip(self.DESTINATION_COLUMN, destination)
            self.addTopLevelItem(item)
            if record.operation_id and record.operation_id == selected_id:
                selected_item = item
        if selected_item is not None:
            self.setCurrentItem(selected_item)
        self._resize_columns()

    def selected_operation_id(self) -> str | None:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(self.TIME_COLUMN, Qt.ItemDataRole.UserRole)

    def selected_record(self) -> ActivityRecord | None:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(
            self.TIME_COLUMN,
            Qt.ItemDataRole.UserRole + 1,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_columns()

    def _resize_columns(self) -> None:
        width = max(0, self.viewport().width())
        hide_category = width < 760
        self.setColumnHidden(self.CATEGORY_COLUMN, hide_category)
        time_width = 112
        result_width = 144 if width >= 760 else 132
        category_width = 128 if not hide_category else 0
        remaining = max(240, width - time_width - result_width - category_width - 8)
        file_width = max(110, int(remaining * 0.38))
        destination_width = max(130, remaining - file_width)
        self.setColumnWidth(self.TIME_COLUMN, time_width)
        self.setColumnWidth(self.FILE_COLUMN, file_width)
        self.setColumnWidth(self.CATEGORY_COLUMN, category_width)
        self.setColumnWidth(self.RESULT_COLUMN, result_width)
        self.setColumnWidth(self.DESTINATION_COLUMN, destination_width)

    @staticmethod
    def _result_tooltip(record: ActivityRecord) -> str:
        details = []
        if record.operation_id:
            details.append(f"Operation ID: {record.operation_id}")
        if record.error:
            details.append(f"Error: {record.error}")
        if record.metadata_warning:
            details.append(f"Warning: {record.metadata_warning}")
        if not record.durable:
            details.append("Live session event; durable confirmation is pending.")
        return "\n".join(details) or "Authoritative operation record"
