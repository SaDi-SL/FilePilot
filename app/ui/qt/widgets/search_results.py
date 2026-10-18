"""Bounded, escaped search evidence with native selection and keyboard support."""
from html import escape
import re

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPalette, QTextDocument
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

HTML_ROLE = int(Qt.ItemDataRole.UserRole) + 1
RESULT_ROLE = int(Qt.ItemDataRole.UserRole) + 2


def highlighted_text(text, query):
    # No raw document HTML reaches Qt's renderer. Match before escaping so query
    # characters such as '&' cannot highlight inside an HTML entity.
    terms = tuple(dict.fromkeys(re.findall(r"\w+", query[:512], re.UNICODE)))[:16]
    terms = tuple(term for term in terms if term.casefold() not in {"and", "or", "not"})
    if not terms:
        return escape(text)
    pattern = re.compile("|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True)), re.IGNORECASE)
    output, start = [], 0
    for match in pattern.finditer(text):
        output.extend((escape(text[start:match.start()]), '<span style="background-color:#57406e;color:#ffffff">', escape(match.group()), '</span>'))
        start = match.end()
    output.append(escape(text[start:]))
    return ''.join(output)


def compact_excerpt(text, *, limit=260):
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


class SearchResultDelegate(QStyledItemDelegate):
    """Let QTextDocument wrap escaped text; retain native list accessibility."""
    def _document(self, option, index):
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setDocumentMargin(8)
        color = option.palette.color(QPalette.ColorRole.Text).name()
        document.setDefaultStyleSheet(f'body {{ color: {color}; }}')
        document.setHtml(index.data(HTML_ROLE) or escape(index.data() or ''))
        document.setTextWidth(max(120, self.parent().viewport().width() - 4))
        return document

    def sizeHint(self, option, index):
        document = self._document(option, index)
        return QSize(120, max(80, int(document.size().height()) + 4))

    def paint(self, painter, option, index):
        native = QStyleOptionViewItem(option)
        self.initStyleOption(native, index)
        native.text = ''
        self.parent().style().drawControl(QStyle.ControlElement.CE_ItemViewItem, native, painter, self.parent())
        document = self._document(option, index)
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.topLeft())
        document.drawContents(painter)
        painter.restore()
