"""
Overlay Window — PySide6 Version
60 FPS HUD overlay synchronized with game window.
"""

import sys
import threading
import os
import ctypes
import time
import win32gui
import win32process

from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from core.dpi_utils import get_dpi_scale_factor
from core.process_manager import get_process_manager

UI_FONT_FAMILY = "Segoe UI"
DETECTION_BOX_COLOR = QColor(170, 80, 255, 230)
DETECTION_BOX_WIDTH = 1
OVERLAY_VISIBILITY_SYNC_MS = 30
OVERLAY_RENDER_TICK_MS = 8
EMPTY_DETECTION_GRACE_MS = 35.0


class OverlayWindow(QWidget):
    """
    High-Performance Overlay with Decoupled Rendering (PySide6).
    """
    detections_updated = Signal(list)
    geometry_updated = Signal(int, int, int, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vision HUD")

        self.default_flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setWindowFlags(self.default_flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.screen_geometry = QApplication.primaryScreen().geometry()
        self.game_geometry = None
        self.my_pid = os.getpid()
        self.blacklist_keywords = ["Vision", "HUD", "Debug", "Dashboard", "Overlay", "FARMACHINE"]

        self.setGeometry(self.screen_geometry)
        self.game_offset_x = 0
        self.game_offset_y = 0
        self.game_width = 0
        self.game_height = 0

        self.dpi_scale = get_dpi_scale_factor()

        self.interactive_mode = False
        self.detections = []
        self._detection_lock = threading.Lock()
        self._last_non_empty_detection_ts = 0.0
        self._empty_detection_grace_ms = EMPTY_DETECTION_GRACE_MS

        # Manual attach highlight (app-owned overlay, no Win32 border drawing)
        self._highlight_hwnd = None
        self._highlight_label = ""
        self._highlight_until = 0.0
        self._highlight_color = QColor(88, 166, 255, 220)

        self.stats = {
            "Status": "Ready",
            "Targets": "0",
            "Time": "00:00:00"
        }

        self.target_titles = ["Rubinum", "Saryong", "Client", "Game"]
        self.process_manager = get_process_manager()

        self.visibility_timer = QTimer(self)
        self.visibility_timer.timeout.connect(self._check_and_sync)
        self.visibility_timer.start(OVERLAY_VISIBILITY_SYNC_MS)

        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self._render_tick)
        self.render_timer.start(OVERLAY_RENDER_TICK_MS)

        self.always_visible = False

    def _render_tick(self):
        with self._detection_lock:
            has_detections = bool(self.detections)
        if has_detections or self._is_highlight_active():
            self.update()

    @staticmethod
    def compute_stats_hud_origin(height_px: int, line_count: int) -> tuple[int, int]:
        """Return (x, y-baseline) for left-middle aligned stats HUD text."""
        safe_height = max(0, int(height_px))
        safe_line_count = max(1, int(line_count))
        line_height = 16

        # drawText uses baseline y, so center the baseline span, not top pixels.
        block_baseline_span = (safe_line_count - 1) * line_height
        center_y = safe_height // 2
        start_y = max(24, int(center_y - (block_baseline_span / 2)))
        return 10, start_y

    def _is_highlight_active(self) -> bool:
        return bool(self._highlight_hwnd) and time.time() < self._highlight_until

    def _clear_highlight(self):
        self._highlight_hwnd = None
        self._highlight_label = ""
        self._highlight_until = 0.0

    def _sync_to_hwnd(self, hwnd: int) -> bool:
        try:
            rect = win32gui.GetClientRect(hwnd)
            pt = win32gui.ClientToScreen(hwnd, (0, 0))
            x, y = pt
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]

            lx = int(x / self.dpi_scale)
            ly = int(y / self.dpi_scale)
            lw = int(w / self.dpi_scale)
            lh = int(h / self.dpi_scale)

            self.game_offset_x = x
            self.game_offset_y = y
            self.game_width = w
            self.game_height = h

            if self.x() != lx or self.y() != ly or self.width() != lw or self.height() != lh:
                self.setGeometry(lx, ly, lw, lh)

            return True
        except Exception:
            return False

    def _on_detections_updated(self, detections):
        now = time.time()
        with self._detection_lock:
            if detections:
                self.detections = detections
                self._last_non_empty_detection_ts = now
            else:
                age_ms = (now - self._last_non_empty_detection_ts) * 1000.0
                if age_ms >= self._empty_detection_grace_ms:
                    self.detections = []
        self.update()

    def clear_detections(self):
        with self._detection_lock:
            self.detections = []
        self.update()

    @Slot(bool)
    def set_captcha_hidden(self, hidden: bool):
        """Hide or show overlay during CAPTCHA to prevent MSS capture pollution."""
        with self._detection_lock:
            self.detections = []
        if hidden:
            self.hide()
        else:
            self.show()

    def _is_locked_window_valid(self, hwnd) -> bool:
        return bool(hwnd and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))

    def _should_skip_window(self, hwnd) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True

        try:
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True

        if window_pid == self.my_pid:
            return True

        return False

    def _get_window_title(self, hwnd) -> str:
        try:
            return win32gui.GetWindowText(hwnd)
        except Exception:
            return ""

    def _is_blacklisted_title(self, title: str) -> bool:
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in self.blacklist_keywords)

    def _is_target_title(self, title: str) -> bool:
        title_lower = title.lower()
        return any(target.lower() in title_lower for target in self.target_titles)

    def _first_valid_locked_hwnd(self):
        active_slot_hwnd = self.process_manager.get_active_slot_hwnd()
        if self._is_locked_window_valid(active_slot_hwnd):
            return active_slot_hwnd

        try:
            for hwnd in self.process_manager.get_locked_hwnds().values():
                if self._is_locked_window_valid(hwnd):
                    return hwnd
        except Exception:
            pass

        locked_hwnd = self.process_manager.get_locked_hwnd()
        if self._is_locked_window_valid(locked_hwnd):
            return locked_hwnd

        return None

    def _find_window_by_title_fallback(self):
        result = [None]

        def callback(hwnd, ctx):
            _ = ctx
            if self._should_skip_window(hwnd):
                return True

            title = self._get_window_title(hwnd)
            if not title:
                return True

            if self._is_blacklisted_title(title):
                return True

            if self._is_target_title(title):
                result[0] = hwnd
                return False

            return True

        try:
            win32gui.EnumWindows(callback, None)
        except Exception:
            pass

        return result[0]

    def _find_game_window(self):
        if self._is_highlight_active() and self._is_locked_window_valid(self._highlight_hwnd):
            return self._highlight_hwnd

        locked_hwnd = self._first_valid_locked_hwnd()
        if locked_hwnd is not None:
            return locked_hwnd

        return self._find_window_by_title_fallback()

    def _check_and_sync(self):
        if self._highlight_hwnd and not self._is_highlight_active():
            self._clear_highlight()

        hwnd = self._find_game_window()
        if hwnd is None:
            if not self.always_visible:
                self.hide()
                self.clear_detections()
            return

        synced = self._sync_to_hwnd(hwnd)
        if synced and not self.isVisible():
            self.show()

    def toggle_interaction(self):
        self.interactive_mode = not self.interactive_mode
        if self.interactive_mode:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        else:
            self.setWindowFlags(self.default_flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.show()

    @Slot(str, str)
    def update_stat(self, key: str, value: str):
        self.stats[key] = value

    @Slot(list)
    def update_detections(self, detections: list):
        self._on_detections_updated(detections)

    @Slot(int, str)
    def highlight_window(self, hwnd: int, label: str):
        if not hwnd:
            return
        if not self._is_locked_window_valid(hwnd):
            return

        self._highlight_hwnd = int(hwnd)
        self._highlight_label = str(label or "Attached")
        self._highlight_until = time.time() + 1.6
        self._highlight_color = QColor(88, 166, 255, 220)

        if "2" in self._highlight_label:
            self._highlight_color = QColor(63, 185, 80, 220)

        self._sync_to_hwnd(self._highlight_hwnd)
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        with self._detection_lock:
            dets = list(self.detections)

        highlight_active = self._is_highlight_active()

        if not dets and not highlight_active:
            painter.end()
            return

        gw = self.game_width if self.game_width > 0 else self.width() * self.dpi_scale
        gh = self.game_height if self.game_height > 0 else self.height() * self.dpi_scale

        for det in dets:
            try:
                rect = det.get("rect", [0, 0, 0, 0])
                label = det.get("label", "")
                conf = det.get("conf", 0)
                x1, y1, x2, y2 = rect

                sx = self.width() / gw if gw > 0 else 1.0
                sy = self.height() / gh if gh > 0 else 1.0

                dx1 = int(x1 * sx)
                dy1 = int(y1 * sy)
                dx2 = int(x2 * sx)
                dy2 = int(y2 * sy)

                pen = QPen(DETECTION_BOX_COLOR, DETECTION_BOX_WIDTH)
                painter.setPen(pen)
                painter.drawRect(dx1, dy1, dx2 - dx1, dy2 - dy1)

                # Prevent duplicated confidence text such as "metin 96% 96%".
                text = label if "%" in str(label) else f"{label} {conf:.0%}"
                painter.setPen(QColor(255, 255, 255))
                painter.setFont(QFont(UI_FONT_FAMILY, 9, QFont.Weight.Bold))
                painter.drawText(dx1 + 4, dy1 - 4, text)
            except Exception:
                continue

        if highlight_active:
            pen = QPen(self._highlight_color, 3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(2, 2, max(0, self.width() - 4), max(0, self.height() - 4), 8, 8)

            chip_bg = QColor(self._highlight_color)
            chip_bg.setAlpha(80)
            chip_rect_w = min(max(150, len(self._highlight_label) * 7), max(150, self.width() - 16))
            chip_rect = (8, 8, chip_rect_w, 26)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(chip_bg)
            painter.drawRoundedRect(chip_rect[0], chip_rect[1], chip_rect[2], chip_rect[3], 6, 6)

            painter.setPen(self._highlight_color)
            painter.setFont(QFont(UI_FONT_FAMILY, 10, QFont.Weight.Bold))
            painter.drawText(chip_rect[0] + 10, chip_rect[1] + 17, self._highlight_label)

        # Stats HUD
        painter.setPen(QColor(200, 200, 200, 180))
        painter.setFont(QFont(UI_FONT_FAMILY, 9))
        x_offset, y_offset = self.compute_stats_hud_origin(self.height(), len(self.stats))
        for key, val in self.stats.items():
            painter.drawText(x_offset, y_offset, f"{key}: {val}")
            y_offset += 16

        painter.end()
