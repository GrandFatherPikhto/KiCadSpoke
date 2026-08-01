# gui/tray_icon.py
"""
build_tray_icon() — a small programmatic QIcon (QPainter onto a QPixmap),
not a binary .ico asset. Matches this project's existing bias toward
human-readable/inspectable artifacts over opaque platform blobs (same
reasoning that already ruled out QSettings for gui/settings.py). Must be
called after a QApplication/QGuiApplication instance exists — QPixmap needs
one.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


def build_tray_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#2b6cb0"))
    painter.setPen(Qt.PenStyle.NoPen)
    margin = size * 0.08
    painter.drawRoundedRect(int(margin), int(margin), int(size - 2 * margin),
                             int(size - 2 * margin), size * 0.2, size * 0.2)

    painter.setPen(QColor("#ffffff"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "K")
    painter.end()

    return QIcon(pixmap)
