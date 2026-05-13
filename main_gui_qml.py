import sys
import threading
import keyboard
import time
import os
import ctypes
from typing import Optional

from ui.qml_style import ensure_qtquickcontrols_style

ensure_qtquickcontrols_style()

# ============================================================
# CRITICAL: Import torch BEFORE PySide6 to prevent DLL conflicts
# ============================================================
import torch

# ============================================================
# High-DPI Scaling Setup (MUST be FIRST - before any GUI/Win32)
# ============================================================
try:
    from core.dpi_utils import (
        set_process_dpi_awareness,
        get_dpi_scale_factor,
        RelativeUI
    )
    dpi_scale = get_dpi_scale_factor()
    print(f"[MAIN] DPI Scale Factor: {dpi_scale:.2f}x ({int(dpi_scale * 100)}%)")
except ImportError:
    print("[MAIN] WARNING: dpi_utils not found, using fallback DPI setup")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()

# PySide6 imports
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QWindow

# Enable High-DPI
QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

# UI Imports
from ui.backend_bridge import BackendBridge
from ui.frameless_window import FramelessMainWindow
from ui.overlay_pyside6 import OverlayWindow

CONFIG_PATH = "config.json"


class MainApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("FARMACHINE")
        self.app.setOrganizationName("FARMACHINE")

        # Backend bridge (single source of truth for QML)
        self.backend = BackendBridge(CONFIG_PATH)

        # Overlay (kept as PyQt5-free PySide6 widget — separate from QML)
        self.overlay = OverlayWindow()

        # Connect bot signals → overlay
        self.backend.bot_signals.update_stat.connect(self.overlay.update_stat)
        self.backend.bot_signals.update_overlay.connect(self.overlay.update_detections)
        self.backend.bot_signals.overlay_hide.connect(self.overlay.set_captcha_hidden)
        self.backend.bot_signals.highlight_window.connect(self.overlay.highlight_window)

        # QML Engine
        self.engine = QQmlApplicationEngine()

        # Expose backend to QML
        self.engine.rootContext().setContextProperty("backend", self.backend)

        # Load QML
        qml_path = os.path.join(os.path.dirname(__file__), "ui", "qml", "Main.qml")
        self.engine.load(QUrl.fromLocalFile(qml_path))

        if not self.engine.rootObjects():
            print("[FATAL] Failed to load QML. Check for syntax errors.")
            sys.exit(1)

        root_object = self.engine.rootObjects()[0]
        if not isinstance(root_object, QWindow):
            print("[FATAL] Root QML object must be a window type.")
            sys.exit(1)

        self.qml_window = root_object
        self.main_window = FramelessMainWindow(self.qml_window)
        self._sync_shell_geometry(self.qml_window)
        self._bind_shell_theme(self.qml_window)
        self.main_window.closing.connect(self.overlay.close)
        self.main_window.closing.connect(self.app.quit)
        self.main_window.show()

        # Setup hotkey for overlay toggle
        keyboard.add_hotkey('insert', self.overlay.toggle_interaction)

        # Initial mouse ID check
        self.check_mouse_id()

        # Auto-scan for windows
        QTimer.singleShot(500, self.backend.refreshWindows)

        # Show overlay
        self.overlay.show()

    @staticmethod
    def _safe_int(value: object, fallback: int, minimum: Optional[int] = None) -> int:
        parsed_value = fallback
        if isinstance(value, bool):
            parsed_value = int(value)
        elif isinstance(value, (int, float)):
            parsed_value = int(value)
        elif isinstance(value, str):
            try:
                parsed_value = int(float(value.strip()))
            except ValueError:
                parsed_value = fallback

        if minimum is not None:
            return max(minimum, parsed_value)
        return parsed_value

    def _sync_shell_geometry(self, qml_window: QWindow) -> None:
        width = self._safe_int(qml_window.property("width"), 1160, minimum=640)
        height = self._safe_int(qml_window.property("height"), 780, minimum=480)
        minimum_width = self._safe_int(qml_window.property("minimumWidth"), 980, minimum=640)
        minimum_height = self._safe_int(qml_window.property("minimumHeight"), 660, minimum=480)

        self.main_window.setMinimumSize(minimum_width, minimum_height)
        self.main_window.resize(width, height)

    def _apply_shell_theme(self, qml_window: QWindow) -> None:
        theme_mode = str(qml_window.property("hostThemeMode") or "dark")
        self.main_window.set_theme_mode(theme_mode)

    def _bind_shell_theme(self, qml_window: QWindow) -> None:
        self._apply_shell_theme(qml_window)
        theme_changed_signal = getattr(qml_window, "hostThemeModeChanged", None)
        if theme_changed_signal is not None:
            theme_changed_signal.connect(lambda: self._apply_shell_theme(qml_window))

    def check_mouse_id(self):
        """Check if mouse ID is configured."""
        try:
            import json
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            mouse_id = config.get("global", {}).get("mouse_id", config.get("system", {}).get("mouse_id", "0"))
            if mouse_id and mouse_id != "0" and int(mouse_id) > 0:
                self.backend._append_log(f"[Config] Mouse ID: {mouse_id}")
                return True
            else:
                self.backend._append_log("[Config] Mouse ID not configured. Use Settings to set it, or run calibration.")
                return False
        except Exception as e:
            self.backend._append_log(f"[ERROR] Mouse ID check: {e}")
            return False

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    main = MainApp()
    main.run()
