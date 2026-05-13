from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent, QShowEvent, QWindow
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class _ThemeColors:
    frame: str
    border: str
    border_light: str
    text: str
    accent: str
    accent_hover: str
    close_hover: str
    title_bar_bg: str
    title_button_bg: str


def _theme_colors(theme_mode: str) -> _ThemeColors:
    normalized_mode = str(theme_mode).strip().lower()
    if normalized_mode == "light":
        return _ThemeColors(
            frame="#EBEFF6",
            border="#D3DBE8",
            border_light="#C4CFDE",
            text="#2D3436",
            accent="#2D9CDB",
            accent_hover="#238BC9",
            close_hover="#D35454",
            title_bar_bg="#E2E8F2",
            title_button_bg="#F9FBFF",
        )

    return _ThemeColors(
        frame="#151625",
        border="#33374F",
        border_light="#434865",
        text="#CDD6F4",
        accent="#4AA3FF",
        accent_hover="#6AB6FF",
        close_hover="#E06C75",
        title_bar_bg="#1E1E2E",
        title_button_bg="#2B2E46",
    )


def build_global_stylesheet(theme_mode: str) -> str:
    """Return the global QSS used for frameless chrome and modern scrollbars."""
    colors = _theme_colors(theme_mode)
    return f"""
QFrame#MainChrome {{
    background-color: {colors.frame};
    border: 1px solid {colors.border};
    border-radius: 14px;
}}

QWidget#CustomTitleBar {{
    background-color: {colors.title_bar_bg};
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    border-bottom: 1px solid {colors.border};
}}

QLabel#WindowTitle {{
    color: {colors.text};
    font-family: Segoe UI Semibold;
    font-size: 16px;
    letter-spacing: 1px;
}}

QPushButton#TitleBarButton,
QPushButton#CloseButton {{
    background-color: {colors.title_button_bg};
    color: {colors.text};
    border: 1px solid {colors.border_light};
    border-radius: 8px;
    font-family: Segoe UI Semibold;
    font-size: 15px;
    font-weight: 700;
    min-width: 32px;
    max-width: 32px;
    min-height: 28px;
    max-height: 28px;
}}

QPushButton#TitleBarButton:hover {{
    background-color: {colors.accent_hover};
    color: #FFFFFF;
}}

QPushButton#CloseButton:hover {{
    background-color: {colors.close_hover};
    color: #FFFFFF;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 0px 2px 0px;
}}

QScrollBar::handle:vertical {{
    background: {colors.accent};
    min-height: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: {colors.accent_hover};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 0px 2px;
}}

QScrollBar::handle:horizontal {{
    background: {colors.accent};
    min-width: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {colors.accent_hover};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    background: transparent;
    border: none;
    width: 0px;
    height: 0px;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QScrollBar::up-arrow:vertical,
QScrollBar::down-arrow:vertical,
QScrollBar::left-arrow:horizontal,
QScrollBar::right-arrow:horizontal {{
    width: 0px;
    height: 0px;
    background: transparent;
}}
"""


class CustomTitleBar(QWidget):
    """Header bar that reproduces native move/minimize/close behavior."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CustomTitleBar")
        self.setFixedHeight(44)

        self._drag_active = False
        self._press_global_pos = QPoint()
        self._window_start_pos = QPoint()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(8)

        self.title_label = QLabel("FARMACHINE", self)
        self.title_label.setObjectName("WindowTitle")
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.minimize_button = QPushButton("-", self)
        self.minimize_button.setObjectName("TitleBarButton")
        self.minimize_button.clicked.connect(self._on_minimize_clicked)
        layout.addWidget(self.minimize_button)

        self.close_button = QPushButton("X", self)
        self.close_button.setObjectName("CloseButton")
        self.close_button.clicked.connect(self._on_close_clicked)
        layout.addWidget(self.close_button)

    def _on_minimize_clicked(self) -> None:
        window = self.window()
        if window is not None:
            window.showMinimized()

    def _on_close_clicked(self) -> None:
        window = self.window()
        if window is not None:
            window.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window is not None:
                self._drag_active = True
                self._press_global_pos = event.globalPosition().toPoint()
                self._window_start_pos = window.pos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
            window = self.window()
            if window is not None:
                delta = event.globalPosition().toPoint() - self._press_global_pos
                window.move(self._window_start_pos + delta)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_active = False
        super().mouseReleaseEvent(event)


class FramelessMainWindow(QWidget):
    """Custom QWidget shell that hosts the QML window as client content."""

    closing = Signal()
    TITLE_BAR_HEIGHT = 44

    def __init__(self, qml_window: QWindow, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._qml_window = qml_window
        self._theme_mode = "dark"

        self.setWindowTitle("FARMACHINE")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(0)

        self.chrome_frame = QFrame(self)
        self.chrome_frame.setObjectName("MainChrome")
        outer_layout.addWidget(self.chrome_frame)

        self.title_bar = CustomTitleBar(self.chrome_frame)
        self.title_bar.setFixedHeight(self.TITLE_BAR_HEIGHT)

        self.content_container = QWidget.createWindowContainer(self._qml_window, self.chrome_frame)
        self.content_container.setObjectName("QmlWindowContainer")
        self.content_container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.set_theme_mode(self._theme_mode)
        self._layout_chrome_children()

    def set_theme_mode(self, theme_mode: str) -> None:
        normalized_mode = "light" if str(theme_mode).strip().lower() == "light" else "dark"
        if normalized_mode == self._theme_mode and self.styleSheet():
            return

        self._theme_mode = normalized_mode
        self.setProperty("themeMode", normalized_mode)

        stylesheet = build_global_stylesheet(normalized_mode)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(stylesheet)
        else:
            self.setStyleSheet(stylesheet)

    def closeEvent(self, event) -> None:
        self.closing.emit()
        self._qml_window.close()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_chrome_children()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._layout_chrome_children()

    def _layout_chrome_children(self) -> None:
        frame_rect = self.chrome_frame.contentsRect()
        title_height = self.title_bar.height()

        self.title_bar.setGeometry(0, 0, frame_rect.width(), title_height)

        content_top = title_height
        content_height = max(0, frame_rect.height() - content_top)
        self.content_container.setGeometry(0, content_top, frame_rect.width(), content_height)

        # Keep title bar above the embedded QML native window container.
        self.title_bar.raise_()
