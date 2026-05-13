"""
Interactive ROI mask selector overlay for a specific game client window.
"""

from __future__ import annotations

from typing import List, Optional

import win32gui
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.dpi_utils import get_dpi_scale_factor


class RegionSelectorOverlay(QWidget):
    """
    Overlay widget for selecting one or more rectangular ROI mask regions.

    Coordinates are persisted in physical client pixels (x, y, width, height)
    so they can be applied directly to captured numpy frames.
    """

    selection_saved = Signal(list)
    selection_cancelled = Signal()

    MIN_REGION_SIZE = 6

    def __init__(self, target_hwnd: int, initial_regions: Optional[List[dict]] = None):
        super().__init__()
        self._target_hwnd = int(target_hwnd)
        self._dpi_scale = max(1e-6, float(get_dpi_scale_factor()))
        self._drag_start: Optional[QPoint] = None
        self._drag_current: Optional[QPoint] = None
        self._regions: List[dict] = []

        self.setWindowTitle("ROI Region Selector")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        if not self._sync_to_target_window():
            raise RuntimeError("Target window could not be resolved")

        if initial_regions:
            self._regions = self._normalize_initial_regions(initial_regions)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _to_physical_px(self, value: int) -> int:
        return int(round(float(value) * self._dpi_scale))

    def _to_logical_px(self, value: int) -> int:
        return int(round(float(value) / self._dpi_scale))

    def _sync_to_target_window(self) -> bool:
        if not win32gui.IsWindow(self._target_hwnd):
            return False

        try:
            client_rect = win32gui.GetClientRect(self._target_hwnd)
            left, top = win32gui.ClientToScreen(self._target_hwnd, (0, 0))
        except Exception:
            return False

        width = int(client_rect[2] - client_rect[0])
        height = int(client_rect[3] - client_rect[1])
        if width <= 0 or height <= 0:
            return False

        self.setGeometry(
            self._to_logical_px(int(left)),
            self._to_logical_px(int(top)),
            max(1, self._to_logical_px(width)),
            max(1, self._to_logical_px(height)),
        )
        return True

    def _clamp_point(self, pt: QPoint) -> QPoint:
        max_x = max(0, self.width() - 1)
        max_y = max(0, self.height() - 1)
        return QPoint(
            max(0, min(max_x, int(pt.x()))),
            max(0, min(max_y, int(pt.y()))),
        )

    def _current_drag_rect(self) -> Optional[QRect]:
        if self._drag_start is None or self._drag_current is None:
            return None
        rect = QRect(self._drag_start, self._drag_current).normalized()
        return rect.intersected(self.rect())

    def _serialize_regions(self) -> List[dict]:
        serialized: List[dict] = []
        for region in self._regions:
            x = max(0, self._to_physical_px(int(region.get("x", 0))))
            y = max(0, self._to_physical_px(int(region.get("y", 0))))
            width = max(1, self._to_physical_px(int(region.get("width", 0))))
            height = max(1, self._to_physical_px(int(region.get("height", 0))))
            serialized.append({"x": x, "y": y, "width": width, "height": height})
        return serialized

    def _normalize_initial_regions(self, regions: List[dict]) -> List[dict]:
        normalized: List[dict] = []
        for item in regions:
            if not isinstance(item, dict):
                continue

            try:
                x = max(0, self._to_logical_px(int(item.get("x", 0))))
                y = max(0, self._to_logical_px(int(item.get("y", 0))))
                width = max(1, self._to_logical_px(int(item.get("width", 0))))
                height = max(1, self._to_logical_px(int(item.get("height", 0))))
            except Exception:
                continue

            normalized.append({"x": x, "y": y, "width": width, "height": height})
        return normalized

    def _save_and_close(self) -> None:
        self.selection_saved.emit(self._serialize_regions())
        self.close()

    def _cancel_and_close(self) -> None:
        self.selection_cancelled.emit()
        self.close()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._save_and_close()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            self._cancel_and_close()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self._regions:
                self._regions.pop()
                self.update()
            event.accept()
            return

        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            if self._regions:
                self._regions.pop()
                self.update()
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        pos = self._clamp_point(event.position().toPoint())
        self._drag_start = pos
        self._drag_current = pos
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return

        self._drag_current = self._clamp_point(event.position().toPoint())
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start is None:
            super().mouseReleaseEvent(event)
            return

        self._drag_current = self._clamp_point(event.position().toPoint())
        rect = self._current_drag_rect()
        self._drag_start = None
        self._drag_current = None

        if rect and rect.width() >= self.MIN_REGION_SIZE and rect.height() >= self.MIN_REGION_SIZE:
            self._regions.append(
                {
                    "x": int(rect.x()),
                    "y": int(rect.y()),
                    "width": int(rect.width()),
                    "height": int(rect.height()),
                }
            )

        self.update()
        event.accept()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        fill_color = QColor(35, 196, 120, 90)
        border_pen = QPen(QColor(35, 196, 120, 220), 2)
        painter.setPen(border_pen)

        for index, region in enumerate(self._regions, start=1):
            rect = QRect(
                int(region["x"]),
                int(region["y"]),
                int(region["width"]),
                int(region["height"]),
            )
            painter.fillRect(rect, fill_color)
            painter.drawRect(rect)
            painter.setPen(QColor(255, 255, 255, 230))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(rect.x() + 6, max(14, rect.y() + 16), f"#{index}")
            painter.setPen(border_pen)

        drag_rect = self._current_drag_rect()
        if drag_rect is not None:
            painter.setPen(QPen(QColor(255, 200, 80, 235), 2))
            painter.fillRect(drag_rect, QColor(255, 200, 80, 80))
            painter.drawRect(drag_rect)

        painter.setPen(QColor(245, 245, 245, 235))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.drawText(
            14,
            24,
            "Draw ROI masks with left-drag. Enter: Save  Esc: Cancel  Right-click/Delete: Undo",
        )

        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Normal))
        painter.drawText(14, 44, f"Current regions: {len(self._regions)}")

        painter.end()
