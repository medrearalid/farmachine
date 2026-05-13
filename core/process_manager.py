"""
Process Manager - Window Enumeration and Process Selection
============================================================
Robust alternative to fragile window title search. This module provides:
- Dynamic enumeration of all visible windows with HWND and PID
- Filtering of game clients from system/bot windows
- Visual flash confirmation for selected windows
- Lock-on by HWND (survives title changes)

Why This Exists:
- Multiple game clients can have the same title
- Window titles can change dynamically (e.g., "Metin2 - Lv 99")
- User needs explicit control over which client to target
"""

import os
import time
import threading
import ctypes
import ctypes.wintypes
from typing import Callable, List, Dict, Optional, Tuple
import win32gui
import win32process
import win32con
import win32api

try:
    import pydirectinput
    PYDIRECTINPUT_AVAILABLE = True
except Exception:
    pydirectinput = None
    PYDIRECTINPUT_AVAILABLE = False


# ============================================================================
# WINDOW INFO DATA CLASS
# ============================================================================

class WindowInfo:
    """
    Represents a discovered window with all relevant metadata.
    """
    def __init__(self, hwnd: int, title: str, pid: int, width: int, height: int):
        self.hwnd = hwnd      # Window Handle (unique identifier)
        self.title = title     # Current window title (may change)
        self.pid = pid         # Process ID (stable)
        self.width = width     # Client area width
        self.height = height   # Client area height
    
    def get_display_name(self) -> str:
        """
        Generate a user-friendly display name for the dropdown.
        Format: "Window Title (1920x1080) [PID: 1234]"
        """
        resolution = f"{self.width}x{self.height}"
        return f"{self.title} ({resolution}) [PID: {self.pid}]"
    
    def get_short_name(self) -> str:
        """Shorter display for compact UIs."""
        return f"{self.title[:25]}... [PID: {self.pid}]" if len(self.title) > 25 else f"{self.title} [PID: {self.pid}]"
    
    def __repr__(self):
        return f"WindowInfo(hwnd={self.hwnd}, title='{self.title}', pid={self.pid}, {self.width}x{self.height})"


# ============================================================================
# PROCESS MANAGER CLASS
# ============================================================================

class ProcessManager:
    """
    Manages window enumeration, selection, and process locking.
    
    Key Features:
    - enumerate_game_windows(): Scan all visible windows
    - flash_window(hwnd): Visual confirmation when selecting
    - get_locked_hwnd(): Returns the user-selected HWND
    """
    
    # Optional title hints that increase candidate score.
    # These are NOT mandatory filters.
    GAME_HINT_KEYWORDS = [
        "metin", "rubinum", "saryong", "game", "client", "gameforge", "m2"
    ]
    
    # Keywords to EXCLUDE (bot's own windows, system windows)
    BLACKLIST_KEYWORDS = [
        "Hunter", "Pivot", "Debug", "Dashboard", "Overlay",
        "FARMACHINE", "Python", "Visual Studio", "Chrome",
        "Edge", "Firefox", "Discord", "Spotify", "Settings"
    ]
    
    # Minimum window size to consider valid (filter out tiny/hidden windows)
    MIN_WIDTH = 800
    MIN_HEIGHT = 500

    SLOT_1 = "client_1"
    SLOT_2 = "client_2"
    VALID_SLOTS = (SLOT_1, SLOT_2)
    SWITCH_FLUSH_DELAY_SEC = 0.05
    FALLBACK_FLUSH_KEYS = (
        "space", "w", "a", "s", "d", "q", "z",
        "1", "2", "3", "4", "5", "g", "ctrl", "f1", "f2", "f3", "f4",
    )
    
    def __init__(self):
        self.my_pid = os.getpid()

        # Backward-compatible single-lock fields (legacy callers)
        self.locked_hwnd: Optional[int] = None
        self.locked_pid: Optional[int] = None
        self.locked_window_info: Optional[WindowInfo] = None

        # Dual-client slot-aware locks
        self.locked_hwnds: Dict[str, Optional[int]] = {
            self.SLOT_1: None,
            self.SLOT_2: None,
        }
        self.HWND_1: Optional[int] = None
        self.HWND_2: Optional[int] = None
        self.locked_pids: Dict[str, Optional[int]] = {
            self.SLOT_1: None,
            self.SLOT_2: None,
        }
        self.locked_window_infos: Dict[str, Optional[WindowInfo]] = {
            self.SLOT_1: None,
            self.SLOT_2: None,
        }
        
        # Cache of discovered windows
        self.discovered_windows: List[WindowInfo] = []
        self.last_scan_time = 0
        self._input_flush_callback: Optional[Callable[[], None]] = None
    
    def enumerate_game_windows(self, include_all: bool = False) -> List[WindowInfo]:  # NOSONAR
        """
        Scan all visible windows and filter for potential game clients.
        
        Args:
            include_all: If True, include all visible windows (not just games)
        
        Returns:
            List of WindowInfo objects for matching windows
        """
        found_windows = []

        def is_likely_client_window(title: str, width: int, height: int) -> bool:
            """Heuristic filter for game-like client windows without title dependency."""
            if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
                return False

            # Typical windowed game ratio range (4:3 to ultrawide-ish)
            aspect_ratio = width / max(1, height)
            if aspect_ratio < 1.15 or aspect_ratio > 2.8:
                return False

            # Avoid obvious system/utility windows via blacklist already applied.
            # Keep this permissive so unknown private-server clients are included.
            return True

        def candidate_score(title: str, width: int, height: int) -> int:
            """Higher score means more likely to be the main playable client window."""
            score = width * height
            lower_title = title.lower()
            if any(hint in lower_title for hint in self.GAME_HINT_KEYWORDS):
                score += 2_000_000
            return score
        
        def enum_callback(hwnd, ctx):  # NOSONAR
            # Skip invisible windows
            if not win32gui.IsWindowVisible(hwnd):
                return True
            
            # Skip minimized windows
            if win32gui.IsIconic(hwnd):
                return True
            
            # Get window title
            try:
                title = win32gui.GetWindowText(hwnd)
            except Exception:
                return True
            
            # Skip windows with empty titles
            if not title or len(title.strip()) == 0:
                return True
            
            # Get window PID
            try:
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return True
            
            # Skip bot's own windows
            if window_pid == self.my_pid:
                return True
            
            # Check blacklist (skip bot/system windows)
            title_lower = title.lower()
            for keyword in self.BLACKLIST_KEYWORDS:
                if keyword.lower() in title_lower:
                    return True
            
            # Get client area dimensions
            try:
                client_rect = win32gui.GetClientRect(hwnd)
                width = client_rect[2]
                height = client_rect[3]
            except Exception:
                width = 0
                height = 0
            
            # Skip tiny windows (likely system windows)
            if width < self.MIN_WIDTH or height < self.MIN_HEIGHT:
                return True
            
            # Title-agnostic client filtering for simple, universal list
            if not include_all and not is_likely_client_window(title, width, height):
                return True
            
            # Valid window found!
            window_info = WindowInfo(
                hwnd=hwnd,
                title=title,
                pid=window_pid,
                width=width,
                height=height
            )
            ctx.append(window_info)
            
            return True  # Continue enumeration
        
        try:
            win32gui.EnumWindows(enum_callback, found_windows)
        except Exception as e:
            print(f"[ProcessManager] Enumeration error: {e}")
        
        # Deduplicate by PID: keep best-scored (usually main render window)
        best_by_pid: Dict[int, WindowInfo] = {}
        for w in found_windows:
            existing = best_by_pid.get(w.pid)
            if existing is None:
                best_by_pid[w.pid] = w
                continue

            current_score = candidate_score(w.title, w.width, w.height)
            existing_score = candidate_score(existing.title, existing.width, existing.height)
            if current_score > existing_score:
                best_by_pid[w.pid] = w

        found_windows = list(best_by_pid.values())

        # Rank probable clients first, then title for stable ordering
        found_windows.sort(
            key=lambda w: (-candidate_score(w.title, w.width, w.height), w.title.lower())
        )
        
        # Update cache
        self.discovered_windows = found_windows
        self.last_scan_time = time.time()
        
        print(f"[ProcessManager] Found {len(found_windows)} game window(s)")
        for w in found_windows:
            print(f"  - {w}")
        
        return found_windows
    
    def flash_window(self, hwnd: int, flash_count: int = 3):
        """
        Draw a visual indicator around the selected window.
        
        This provides clear feedback to the user about which
        window they are selecting. Uses FLASHW_ALL for maximum visibility.
        
        Args:
            hwnd: Window handle to flash
            flash_count: Number of times to flash (default 3)
        """
        try:
            # Bring window to foreground briefly
            win32gui.SetForegroundWindow(hwnd)
            
            # Flash the window using Windows API
            # FLASHW_ALL = 3 (flash both caption and taskbar)
            # FLASHW_TIMERNOFG = 12 (flash until window comes to foreground)
            
            # Use FlashWindowEx for better control
            try:
                # Define FLASHWINFO structure
                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ('cbSize', ctypes.wintypes.UINT),
                        ('hwnd', ctypes.wintypes.HWND),
                        ('dwFlags', ctypes.wintypes.DWORD),
                        ('uCount', ctypes.wintypes.UINT),
                        ('dwTimeout', ctypes.wintypes.DWORD)
                    ]
                
                flash_info = FLASHWINFO()
                flash_info.cbSize = ctypes.sizeof(FLASHWINFO)
                flash_info.hwnd = hwnd
                flash_info.dwFlags = 3  # FLASHW_ALL
                flash_info.uCount = flash_count
                flash_info.dwTimeout = 0  # Use default cursor blink rate
                
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(flash_info))
                
            except Exception:
                # Fallback: Simple flash
                for _ in range(flash_count):
                    win32gui.FlashWindow(hwnd, True)
                    time.sleep(0.2)
                    win32gui.FlashWindow(hwnd, False)
                    time.sleep(0.2)
            
            print(f"[ProcessManager] Flashed window HWND: {hwnd}")
            
        except Exception as e:
            print(f"[ProcessManager] Flash error: {e}")
    
    def draw_border(self, hwnd: int, color: Tuple[int, int, int] = (255, 0, 0), duration: float = 1.0):
        """
        Draw a temporary colored border around the window.
        
        This is more visual than FlashWindow and clearly shows
        which exact window is being selected.
        
        Args:
            hwnd: Window handle
            color: RGB tuple (default red)
            duration: How long to show the border in seconds
        """
        try:
            # Get window DC (device context)
            hdc = win32gui.GetWindowDC(hwnd)
            if not hdc:
                print("[ProcessManager] Could not get window DC")
                return
            
            # Get window rect for border dimensions
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            
            # Create a pen for the border
            # Color format is BGR for Windows GDI
            pen_color = win32api.RGB(color[0], color[1], color[2])
            pen = win32gui.CreatePen(win32con.PS_SOLID, 4, pen_color)
            null_brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
            
            # Select the pen and draw rectangle
            old_pen = win32gui.SelectObject(hdc, pen)  # type: ignore[arg-type]
            old_brush = win32gui.SelectObject(hdc, null_brush)  # type: ignore[arg-type]
            
            win32gui.Rectangle(hdc, 0, 0, width, height)
            
            # Restore old objects
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(pen)
            
            # Release DC
            win32gui.ReleaseDC(hwnd, hdc)
            
            print(f"[ProcessManager] Drew border on HWND: {hwnd}")
            
            # Schedule border removal (by invalidating the window)
            def remove_border():
                try:
                    time.sleep(duration)
                    # Trigger repaint to clear temporary overlay drawing.
                    win32gui.UpdateWindow(hwnd)
                except Exception:
                    pass
            
            # Run in background thread to not block
            threading.Thread(target=remove_border, daemon=True).start()
            
        except Exception as e:
            print(f"[ProcessManager] Border draw error: {e}")
    
    def lock_to_window(self, window_info: WindowInfo) -> bool:
        """
        Lock the bot to a specific window by HWND.
        
        Once locked, the bot will use this HWND even if the
        window title changes. This provides stable targeting.
        
        Args:
            window_info: The WindowInfo object to lock to
        
        Returns:
            True if successfully locked, False otherwise
        """
        return self.lock_to_slot(self.SLOT_1, window_info)

    def lock_to_slot(self, slot: str, window_info: WindowInfo) -> bool:
        """
        Lock a specific client slot to a window.

        Args:
            slot: One of 'client_1' or 'client_2'
            window_info: The target window metadata
        """
        if slot not in self.VALID_SLOTS:
            print(f"[ProcessManager] Invalid slot: {slot}")
            return False

        try:
            if not win32gui.IsWindow(window_info.hwnd):
                print(f"[ProcessManager] Window no longer exists: {window_info.hwnd}")
                return False

            self.locked_hwnds[slot] = window_info.hwnd
            if slot == self.SLOT_1:
                self.HWND_1 = window_info.hwnd
            elif slot == self.SLOT_2:
                self.HWND_2 = window_info.hwnd
            self.locked_pids[slot] = window_info.pid
            self.locked_window_infos[slot] = window_info

            # Keep legacy fields in sync with slot-1 lock.
            if slot == self.SLOT_1:
                self.locked_hwnd = window_info.hwnd
                self.locked_pid = window_info.pid
                self.locked_window_info = window_info

            print(f"[ProcessManager] Locked {slot} to window: {window_info}")
            self.flash_window(window_info.hwnd)
            return True
        except Exception as e:
            print(f"[ProcessManager] Lock error ({slot}): {e}")
            return False

    def unlock(self, slot: Optional[str] = None):
        """
        Release slot lock(s).

        Args:
            slot: Optional slot name. If omitted, all slots are released.
        """
        if slot is None:
            for s in self.VALID_SLOTS:
                self.locked_hwnds[s] = None
                self.locked_pids[s] = None
                self.locked_window_infos[s] = None
            self.HWND_1 = None
            self.HWND_2 = None

            self.locked_hwnd = None
            self.locked_pid = None
            self.locked_window_info = None
            print("[ProcessManager] All window locks released")
            return

        if slot not in self.VALID_SLOTS:
            print(f"[ProcessManager] Invalid slot unlock request: {slot}")
            return

        self.locked_hwnds[slot] = None
        if slot == self.SLOT_1:
            self.HWND_1 = None
        elif slot == self.SLOT_2:
            self.HWND_2 = None
        self.locked_pids[slot] = None
        self.locked_window_infos[slot] = None

        if slot == self.SLOT_1:
            self.locked_hwnd = None
            self.locked_pid = None
            self.locked_window_info = None

        print(f"[ProcessManager] Window lock released for {slot}")

    def get_locked_hwnd(self, slot: Optional[str] = None) -> Optional[int]:
        """
        Get the currently locked HWND.
        
        Args:
            slot: Optional slot id. If omitted, returns slot-1 lock for backward compatibility.

        Returns:
            The locked HWND, or None if not locked
        """
        if slot is not None:
            if slot not in self.VALID_SLOTS:
                return None
            hwnd = self.locked_hwnds.get(slot)
            if hwnd and win32gui.IsWindow(hwnd):
                return hwnd
            return None

        # Backward-compatible behavior -> slot 1
        if self.locked_hwnd and win32gui.IsWindow(self.locked_hwnd):
            return self.locked_hwnd
        return None

    def get_locked_hwnds(self) -> Dict[str, Optional[int]]:
        """Return all currently tracked slot locks as a copy."""
        result: Dict[str, Optional[int]] = {}
        for slot in self.VALID_SLOTS:
            hwnd = self.locked_hwnds.get(slot)
            result[slot] = hwnd if hwnd and win32gui.IsWindow(hwnd) else None
        return result

    def get_active_slot_hwnd(self) -> Optional[int]:
        """
        Return foreground HWND if it belongs to tracked slots.
        """
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            return None

        if not fg_hwnd:
            return None

        for hwnd in self.get_locked_hwnds().values():
            if hwnd and hwnd == fg_hwnd:
                return fg_hwnd

        return None

    def _force_focus_fallback(self, target_hwnd: int) -> None:
        """
        Use additional Win32 APIs when SetForegroundWindow alone is insufficient.
        """
        user32 = ctypes.windll.user32

        fg_hwnd = win32gui.GetForegroundWindow()
        current_thread_id = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        target_thread_id = win32process.GetWindowThreadProcessId(target_hwnd)[0]

        attached = False
        try:
            if current_thread_id and target_thread_id and current_thread_id != target_thread_id:
                user32.AttachThreadInput(current_thread_id, target_thread_id, True)
                attached = True

            win32gui.BringWindowToTop(target_hwnd)
            win32gui.SetForegroundWindow(target_hwnd)
            win32gui.SetActiveWindow(target_hwnd)
            user32.SetFocus(target_hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread_id, target_thread_id, False)

    def set_input_flush_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """
        Register a callable that performs a blind input UP flush.

        Expected callable signature: () -> None
        """
        self._input_flush_callback = callback

    def _fallback_release_all_inputs(self) -> None:
        if not PYDIRECTINPUT_AVAILABLE or pydirectinput is None:
            return

        for key_name in self.FALLBACK_FLUSH_KEYS:
            try:
                pydirectinput.keyUp(key_name)
            except Exception:
                pass

        for button in ("left", "right"):
            try:
                pydirectinput.mouseUp(button=button)
            except Exception:
                pass

    def _run_input_flush(self) -> None:
        if self._input_flush_callback:
            try:
                self._input_flush_callback()
                return
            except Exception as e:
                print(f"[ProcessManager] Input flush callback error: {e}")

        self._fallback_release_all_inputs()

    def switch_context(self, target_hwnd: int, timeout_ms: int = 500) -> bool:
        """
        Bring target client to foreground and block until focus is confirmed.

        CRITICAL SAFETY GATE:
        Do not return success until GetForegroundWindow() equals target_hwnd,
        or timeout is reached.

        Args:
            target_hwnd: Target game client HWND
            timeout_ms: Max wait for focus confirmation
        """
        if not target_hwnd or not win32gui.IsWindow(target_hwnd):
            return False

        try:
            if win32gui.IsIconic(target_hwnd):
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        current_foreground_hwnd: Optional[int] = None
        try:
            current_foreground_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            current_foreground_hwnd = None

        # Fast path: already in foreground
        if current_foreground_hwnd == target_hwnd:
            return True

        # Phase 2: pre-switch flush barrier while old client is still focused.
        self._run_input_flush()
        time.sleep(self.SWITCH_FLUSH_DELAY_SEC)

        # First attempt: direct foreground call
        try:
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            pass

        deadline = time.time() + (max(1, timeout_ms) / 1000.0)

        # If direct attempt failed, try fallback once.
        if win32gui.GetForegroundWindow() != target_hwnd:
            try:
                self._force_focus_fallback(target_hwnd)
            except Exception:
                pass

        while time.time() < deadline:
            try:
                if win32gui.GetForegroundWindow() == target_hwnd:
                    # Phase 4: post-switch failsafe flush on newly focused client.
                    self._run_input_flush()
                    return True
            except Exception:
                return False

            time.sleep(0.01)

        return False
    
    def is_locked(self, slot: Optional[str] = None) -> bool:
        """Check if a window is currently locked for a slot or for any slot."""
        if slot is not None:
            return self.get_locked_hwnd(slot=slot) is not None
        return any(hwnd is not None for hwnd in self.get_locked_hwnds().values())
    
    def get_locked_window_info(self, slot: Optional[str] = None) -> Optional[WindowInfo]:
        """
        Get the locked window's info, refreshed with current data.

        Args:
            slot: Optional slot id. If omitted, uses slot-1 for backward compatibility.
        
        Returns:
            Updated WindowInfo or None if not locked/invalid
        """
        slot_id = self.SLOT_1 if slot is None else slot
        if slot_id not in self.VALID_SLOTS:
            return None

        locked_hwnd = self.get_locked_hwnd(slot=slot_id)
        if not locked_hwnd:
            return None
        
        if not win32gui.IsWindow(locked_hwnd):
            self.unlock(slot=slot_id)
            return None
        
        # Refresh the window info with current data
        try:
            title = win32gui.GetWindowText(locked_hwnd)
            _, pid = win32process.GetWindowThreadProcessId(locked_hwnd)
            client_rect = win32gui.GetClientRect(locked_hwnd)
            
            refreshed_info = WindowInfo(
                hwnd=locked_hwnd,
                title=title,
                pid=pid,
                width=client_rect[2],
                height=client_rect[3]
            )

            self.locked_window_infos[slot_id] = refreshed_info
            if slot_id == self.SLOT_1:
                self.locked_window_info = refreshed_info
            return refreshed_info
            
        except Exception as e:
            print(f"[ProcessManager] Error refreshing window info: {e}")
            return self.locked_window_infos.get(slot_id)


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================
# Single shared instance for the entire application
_process_manager: Optional[ProcessManager] = None


def get_process_manager() -> ProcessManager:
    """Get or create the global ProcessManager instance."""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager()
    return _process_manager


# ============================================================================
# STANDALONE TEST
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Process Manager - Window Enumeration Test")
    print("=" * 60)
    
    pm = get_process_manager()
    
    # Test enumeration
    print("\n[TEST] Scanning for game windows...")
    windows = pm.enumerate_game_windows(include_all=False)
    
    if not windows:
        print("\n[TEST] No game windows found. Trying all windows...")
        windows = pm.enumerate_game_windows(include_all=True)
    
    if windows:
        print(f"\n[TEST] Found {len(windows)} window(s):")
        for i, w in enumerate(windows):
            print(f"  {i+1}. {w.get_display_name()}")
        
        # Test flash on first window
        print(f"\n[TEST] Flashing first window: {windows[0].title}")
        pm.flash_window(windows[0].hwnd)
        
        time.sleep(1)
        
        # Test border draw
        print("[TEST] Drawing red border on first window...")
        pm.draw_border(windows[0].hwnd, color=(255, 0, 0), duration=2.0)
        
        # Test lock
        print("\n[TEST] Locking to first window...")
        pm.lock_to_window(windows[0])
        print(f"[TEST] Locked HWND: {pm.get_locked_hwnd()}")
        
    else:
        print("[TEST] No windows found at all!")
    
    print("\n[TEST] Complete!")
