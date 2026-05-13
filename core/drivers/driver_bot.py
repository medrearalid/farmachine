"""
Driver Bot Module
Python wrapper for driver_bot.dll using ctypes for hardware mouse control.
"""

import ctypes
import os
import sys
import time
import random
import threading
from pathlib import Path
from typing import Callable, Optional

from core.drivers.interception_handler import (
    InterceptionKeyStroke,
    InterceptionMouseStroke,
    interception,
    find_active_mouse_silent,
    auto_detect_mouse_id,
)

# Import pydirectinput for right-click fallback (DLL may not support it)
try:
    import pydirectinput
    PYDIRECTINPUT_AVAILABLE = True
except ImportError:
    pydirectinput = None
    PYDIRECTINPUT_AVAILABLE = False
    print("[DriverBot] [WARN] pydirectinput not available, right_click will not work")


INTERCEPTION_MOUSE_LEFT_BUTTON_UP = 0x002
INTERCEPTION_MOUSE_RIGHT_BUTTON_UP = 0x008
INTERCEPTION_KEY_UP = 0x01

# Blind safety flush list (movement + attack + frequently used action keys).
FLUSH_KEY_SCANCODES = (
    0x39,  # space
    0x11,  # w
    0x1E,  # a
    0x1F,  # s
    0x20,  # d
    0x10,  # q
    0x2C,  # z
    0x02,  # 1
    0x03,  # 2
    0x04,  # 3
    0x05,  # 4
    0x06,  # 5
    0x22,  # g
    0x1D,  # left ctrl
)

FLUSH_KEY_NAMES = (
    "space",
    "w",
    "a",
    "s",
    "d",
    "q",
    "z",
    "1",
    "2",
    "3",
    "4",
    "5",
    "g",
    "ctrl",
    "f1",
    "f2",
    "f3",
    "f4",
)


class DriverBot:
    """
    Hardware mouse control via Interception driver (driver_bot.dll).
    """
    
    def __init__(self, mouse_id: int, log_callback: Optional[Callable[[str], None]] = None):
        """
        Initialize the driver bot with a specific mouse device ID.
        
        Args:
            mouse_id: The device ID of the mouse (e.g., 11)
            
        Raises:
            OSError: If driver_bot.dll cannot be loaded
            Exception: If initialization fails
        """
        self.mouse_id = int(mouse_id)
        self.log_callback = log_callback
        self._lock = threading.RLock()
        self._is_reconnecting = False
        self._health_context = None

        self.driver = self._load_driver()
        self._initialize_driver()
        self._create_health_context()

    def _log(self, message: str):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def _mouse_id_file_path(self) -> Path:
        # core/drivers/driver_bot.py -> project root -> data/mouse_id.txt
        return Path(__file__).resolve().parents[2] / "data" / "mouse_id.txt"

    def _write_mouse_id_file(self):
        try:
            mouse_id_path = self._mouse_id_file_path()
            mouse_id_path.parent.mkdir(parents=True, exist_ok=True)
            mouse_id_path.write_text(f"{self.mouse_id}\n", encoding="utf-8")
        except Exception as e:
            self._log(f"[DRIVER] [WARN] mouse_id.txt yazilamadi: {e}")

    def _create_health_context(self):
        if self._health_context:
            interception.interception_destroy_context(self._health_context)
            self._health_context = None

        self._health_context = interception.interception_create_context()
        if not self._health_context:
            raise RuntimeError("Interception context olusturulamadi")

    def _is_mouse_id_active(self) -> bool:
        try:
            return bool(interception.interception_is_mouse(int(self.mouse_id)))
        except Exception:
            return False

    def _reload_driver_library(self):
        """Force-reset DLL global state to recover from device/context desync."""
        old_driver = self.driver
        old_handle = getattr(old_driver, "_handle", None)

        if old_handle:
            kernel32 = ctypes.windll.kernel32
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel32.FreeLibrary.restype = ctypes.c_int
            kernel32.FreeLibrary(ctypes.c_void_p(old_handle))

        self.driver = self._load_driver()
        self._initialize_driver()

    def reconnect(self, reason: str = "") -> bool:
        """
        Self-healing reconnection flow.

        1) Destroy current context safely
        2) Create a new context
        3) Detect a new active mouse ID (silent, non-blocking)
        4) Update in-memory device and persist to data/mouse_id.txt
        """
        with self._lock:
            if self._is_reconnecting:
                return False

            self._is_reconnecting = True
            try:
                if reason:
                    self._log(f"[DRIVER] [WARN] Baglanti koptu, yeniden baglaniliyor: {reason}")
                else:
                    self._log("[DRIVER] [WARN] Baglanti koptu, yeniden baglaniliyor")

                # 1) Destroy existing context safely
                if self._health_context:
                    interception.interception_destroy_context(self._health_context)
                    self._health_context = None

                # 2) Create fresh context
                self._create_health_context()

                # 3) Find first active mouse ID quickly
                new_mouse_id = find_active_mouse_silent()
                if not new_mouse_id:
                    self._log("[DRIVER] [INFO] Sessiz tespit basarisiz. Hareket tabanli tespit deneniyor (2s)...")
                    new_mouse_id = auto_detect_mouse_id(timeout_seconds=2)

                if not new_mouse_id:
                    self._log("[DRIVER] [HATA] Aktif mouse ID bulunamadi")
                    return False

                # 4) Apply and persist
                self.mouse_id = int(new_mouse_id)
                self._reload_driver_library()
                self.driver.set_device(ctypes.c_int(self.mouse_id))
                self._write_mouse_id_file()
                self._log(f"[DRIVER] [OK] Reconnect tamamlandi. Yeni Mouse ID: {self.mouse_id}")
                return True

            except Exception as e:
                self._log(f"[DRIVER] [HATA] Reconnect basarisiz: {e}")
                return False
            finally:
                self._is_reconnecting = False

    def update_mouse_id(self, mouse_id: int, reason: str = "manual") -> bool:
        """
        Apply a known mouse ID immediately and persist runtime state.
        """
        with self._lock:
            try:
                self.mouse_id = int(mouse_id)
                self._reload_driver_library()
                self.driver.set_device(ctypes.c_int(self.mouse_id))
                self._write_mouse_id_file()
                self._log(f"[DRIVER] [OK] Mouse ID guncellendi ({reason}): {self.mouse_id}")
                return True
            except Exception as e:
                self._log(f"[DRIVER] [HATA] Mouse ID guncellenemedi ({reason}): {e}")
                return False

    def _run_with_self_heal(self, operation_name: str, operation: Callable[[], None]):
        with self._lock:
            try:
                # Fast pre-check before sending command
                if not self._is_mouse_id_active():
                    raise RuntimeError(f"Mouse ID aktif degil: {self.mouse_id}")

                operation()
                return

            except Exception as first_error:
                self._log(f"[DRIVER] [WARN] {operation_name} hatasi: {first_error}")

                if not self.reconnect(reason=f"{operation_name} failed"):
                    raise RuntimeError(f"{operation_name} basarisiz ve reconnect olmadi") from first_error

                # Retry exactly once after reconnect
                operation()
    
    def _load_driver(self):
        """
        Load driver_bot.dll from the current directory.
        
        Returns:
            ctypes.CDLL object representing the loaded DLL
            
        Raises:
            OSError: If DLL cannot be loaded
        """
        script_dir = Path(__file__).parent
        dll_path = script_dir / "driver_bot.dll"
        
        if not dll_path.exists():
            raise FileNotFoundError(
                f"driver_bot.dll not found at: {dll_path}\n"
                "Make sure driver_bot.dll is in the same directory as this script."
            )
        
        try:
            # Add script directory to DLL search path for dependencies
            os.add_dll_directory(str(script_dir))
            
            # Load the DLL
            driver = ctypes.CDLL(str(dll_path))
            self._log(f"[DriverBot] [OK] Loaded driver_bot.dll from {dll_path}")
            return driver
        
        except OSError as e:
            raise OSError(
                f"Failed to load driver_bot.dll: {e}\n"
                f"Make sure Interception driver is installed and the DLL is valid."
            )
    
    def _initialize_driver(self):
        """
        Initialize the driver and set up function signatures.
        
        Raises:
            Exception: If initialization fails
        """
        try:
            # Define function signatures
            self.driver.init.argtypes = []
            self.driver.init.restype = ctypes.c_int
            
            self.driver.set_device.argtypes = [ctypes.c_int]
            self.driver.set_device.restype = None
            
            self.driver.move_abs.argtypes = [ctypes.c_int, ctypes.c_int]
            self.driver.move_abs.restype = None
            
            self.driver.driver_click.argtypes = [ctypes.c_int]
            self.driver.driver_click.restype = None
            
            # Initialize Interception context
            result = self.driver.init()
            if result == 0:
                self._log("[DriverBot] [OK] Interception context initialized")
            else:
                self._log(f"[DriverBot] [WARN] init() returned code: {result}")
            
            # Set device ID
            self.driver.set_device(ctypes.c_int(self.mouse_id))
            self._log(f"[DriverBot] [OK] Device ID set to {self.mouse_id}")
        
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize driver: {e}\n"
                f"Check that Interception driver is properly installed."
            )
    
    def _get_cursor_pos(self) -> Optional[tuple[int, int]]:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        try:
            ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            if not ok:
                return None
            return int(point.x), int(point.y)
        except Exception:
            return None

    def move_abs(self, x: int, y: int, interpolate: bool = True):
        """
        Move mouse to absolute screen coordinates.
        
        Args:
            x: X coordinate (pixels)
            y: Y coordinate (pixels)
        """
        target_x = int(x)
        target_y = int(y)

        def operation():
            if not interpolate:
                self.driver.move_abs(ctypes.c_int(target_x), ctypes.c_int(target_y))
                return

            start_pos = self._get_cursor_pos()
            if start_pos is None:
                self.driver.move_abs(ctypes.c_int(target_x), ctypes.c_int(target_y))
                return

            start_x, start_y = start_pos
            dist = ((target_x - start_x) ** 2 + (target_y - start_y) ** 2) ** 0.5
            if dist < 8:
                self.driver.move_abs(ctypes.c_int(target_x), ctypes.c_int(target_y))
                return

            steps = random.randint(2, 4)
            total_duration = random.uniform(0.050, 0.095)
            step_sleep = total_duration / float(steps)

            for i in range(1, steps + 1):
                t = i / float(steps)
                # Ease-out progression for human-like hand motion.
                eased = 1.0 - ((1.0 - t) ** 2)

                nx = start_x + (target_x - start_x) * eased
                ny = start_y + (target_y - start_y) * eased

                if i < steps:
                    nx += random.uniform(-1.0, 1.0)
                    ny += random.uniform(-1.0, 1.0)

                self.driver.move_abs(ctypes.c_int(int(nx)), ctypes.c_int(int(ny)))
                if step_sleep > 0:
                    time.sleep(step_sleep)

            self.driver.move_abs(ctypes.c_int(target_x), ctypes.c_int(target_y))

        self._run_with_self_heal("move_abs", operation)
    
    def click(self, duration_ms: int = 30):
        """
        Execute a mouse click with variable duration.
        
        Args:
            duration_ms: Duration of the click press in milliseconds (default 30ms)
            
        Raises:
            Exception: If click fails
        """
        def operation():
            self.driver.driver_click(ctypes.c_int(duration_ms))

        self._run_with_self_heal("click", operation)
    
    def right_click(self, duration_ms: int = 100):
        """
        Execute a right mouse click using pydirectinput.
        
        The Interception driver DLL may not support right-click natively,
        so we use pydirectinput as a reliable fallback for game input.
        
        Args:
            duration_ms: Duration of the click press in milliseconds (default 100ms)
            
        Raises:
            Exception: If right-click fails
        """
        if not PYDIRECTINPUT_AVAILABLE or pydirectinput is None:
            raise RuntimeError("pydirectinput not available for right_click")

        def operation():
            assert pydirectinput is not None
            # Use pydirectinput for right-click (more game-compatible)
            pydirectinput.mouseDown(button='right')

            # Human-like random hold duration
            hold_time = duration_ms / 1000.0
            if hold_time < 0.05:
                hold_time = random.uniform(0.05, 0.10)
            time.sleep(hold_time)

            pydirectinput.mouseUp(button='right')

        self._run_with_self_heal("right_click", operation)

    def _send_interception_mouse_flush(self, context: ctypes.c_void_p):
        for button_up_state in (INTERCEPTION_MOUSE_LEFT_BUTTON_UP, INTERCEPTION_MOUSE_RIGHT_BUTTON_UP):
            stroke = InterceptionMouseStroke()
            stroke.state = int(button_up_state)
            stroke.flags = 0
            stroke.rolling = 0
            stroke.x = 0
            stroke.y = 0
            stroke.information = 0
            interception.interception_send(context, int(self.mouse_id), ctypes.byref(stroke), 1)

    def _send_interception_keyboard_flush(self, context: ctypes.c_void_p):
        keyboard_devices = []
        for device_id in range(1, 21):
            try:
                if interception.interception_is_keyboard(device_id):
                    keyboard_devices.append(device_id)
            except Exception:
                continue

        # Fall back to canonical keyboard slots if detection fails.
        if not keyboard_devices:
            keyboard_devices = list(range(1, 11))

        for device_id in keyboard_devices:
            for scan_code in FLUSH_KEY_SCANCODES:
                key_stroke = InterceptionKeyStroke()
                key_stroke.code = int(scan_code)
                key_stroke.state = int(INTERCEPTION_KEY_UP)
                key_stroke.information = 0
                interception.interception_send(context, int(device_id), ctypes.byref(key_stroke), 1)

    def release_all_inputs(self):
        """
        Blind safety flush for stuck-input prevention.

        Sends UP events regardless of tracked key state.
        """
        with self._lock:
            context = None

            try:
                context = interception.interception_create_context()
                if context:
                    self._send_interception_mouse_flush(context)
                    self._send_interception_keyboard_flush(context)
                else:
                    self._log("[DRIVER] [WARN] release_all_inputs: interception context olusturulamadi")
            except Exception as e:
                self._log(f"[DRIVER] [WARN] release_all_inputs interception hatasi: {e}")
            finally:
                if context:
                    try:
                        interception.interception_destroy_context(context)
                    except Exception:
                        pass

            # Extra compatibility flush for apps that ignore Interception on release edge cases.
            if PYDIRECTINPUT_AVAILABLE and pydirectinput is not None:
                for key_name in FLUSH_KEY_NAMES:
                    try:
                        pydirectinput.keyUp(key_name)
                    except Exception:
                        pass

                for button in ("left", "right"):
                    try:
                        pydirectinput.mouseUp(button=button)
                    except Exception:
                        pass
    
    def close(self):
        """
        Clean up driver resources (if needed).
        """
        with self._lock:
            if self._health_context:
                interception.interception_destroy_context(self._health_context)
                self._health_context = None

