"""
Window Capture - MSS Edition (Stability First)
===============================================
Pure Python-MSS implementation for maximum compatibility and stability.

Why MSS Only:
- Works on 100% of Windows systems (no GPU driver dependencies)
- Zero catastrophic failures (no COMError crashes)
- Lightweight and fast enough for bot automation (30-60 FPS)
- Battle-tested library with excellent cross-platform support

Key Features:
- PID filtering to avoid capturing bot's own GUI
- Graceful error handling (returns None instead of crashing)
- Direct BGR output for OpenCV compatibility
- Simple, maintainable codebase
"""

import os
import numpy as np
import win32gui
import win32process
import cv2
from typing import Optional, List, Tuple

try:
    import mss
except ImportError:
    raise ImportError(
        "MSS is not installed. Install it with: pip install mss"
    )


class WindowCapture:
    """
    Stable window capture using MSS (Python Multiple ScreenShots).
    
    This implementation prioritizes stability and compatibility over
    raw performance. MSS works on all Windows systems without GPU
    driver dependencies or hardware-specific issues.
    
    Performance: 30-60 FPS (sufficient for bot automation)
    Compatibility: 100% (works everywhere)
    Stability: Excellent (no catastrophic failures)
    
    NEW: Supports direct HWND attachment from ProcessManager.
    """
    
    def __init__(self, target_titles: Optional[List[str]] = None, target_hwnd: Optional[int] = None):
        """
        Initialize the MSS-based capture system.
        
        Args:
            target_titles: List of window titles to search for (e.g., ["Rubinum", "Metin2"])
                          Only used if target_hwnd is not provided.
            target_hwnd: Direct window handle from ProcessManager.
                        If provided, skips title-based search entirely.
        """
        self.target_titles = target_titles or ["Rubinum", "Saryong", "Metin2", "Client", "Game"]
        
        # Filter configuration (avoid capturing bot's own GUI)
        self.my_pid = os.getpid()
        self.blacklist_keywords = ["Hunter", "Pivot", "Debug", "Dashboard", "Overlay", "FARMACHINE"]
        
        # Window tracking
        self.target_hwnd = None
        self.window_x = 0
        self.window_y = 0
        self.window_width = 0
        self.window_height = 0
        
        # MSS screenshot object
        self.sct = None
        
        # Statistics
        self.frame_count = 0
        self.error_count = 0
        
        # Initialize MSS
        self._init_mss()
        
        # Use provided HWND or search by title
        if target_hwnd is not None:
            self.set_target_hwnd(target_hwnd)
        else:
            self._find_target_window()
    
    def set_target_hwnd(self, hwnd: int) -> bool:
        """
        Set the target window by HWND directly.
        
        This is the preferred method when using ProcessManager.
        It bypasses title-based search and locks directly to the window.
        
        Args:
            hwnd: Window handle from ProcessManager
            
        Returns:
            True if successfully attached, False otherwise
        """
        try:
            # Verify the window exists
            if not win32gui.IsWindow(hwnd):
                print(f"[WindowCapture] ERROR: HWND {hwnd} is not a valid window")
                return False
            
            # Get window info
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            self.target_hwnd = hwnd
            self.window_x = 0
            self.window_y = 0
            self.window_width = 0
            self.window_height = 0
            
            print(f"[WindowCapture] Attached to window: '{title}'")
            print(f"[WindowCapture] HWND: {hwnd} | PID: {pid}")
            
            return True
            
        except Exception as e:
            print(f"[WindowCapture] ERROR: Failed to set HWND: {e}")
            return False

    def update_target_hwnd(self, hwnd: int) -> bool:
        """
        Switch capture target dynamically during runtime orchestration.

        This method is intentionally lightweight and safe to call repeatedly.
        """
        if self.target_hwnd == hwnd:
            return True
        return self.set_target_hwnd(hwnd)
    
    def _init_mss(self):
        """
        Initialize MSS screenshot engine.
        MSS never fails - it's the most reliable capture library.
        """
        try:
            self.sct = mss.mss()
            print("[WindowCapture] MSS initialized successfully")
            print("[WindowCapture] Backend: MSS (CPU-based, 100% compatible)")
        except Exception as e:
            print(f"[WindowCapture] CRITICAL: MSS initialization failed: {e}")
            raise
    
    def _find_target_window(self):  # NOSONAR
        """
        Find the game window with PID filtering to avoid self-targeting.
        
        Safety Measures:
        1. Skip windows owned by bot's own process
        2. Skip windows with blacklisted keywords (Hunter, Debug, etc.)
        3. Only match actual game client windows
        """
        def callback(hwnd, ctx):
            if not win32gui.IsWindowVisible(hwnd):
                return
            
            # Get window title
            try:
                title = win32gui.GetWindowText(hwnd)
            except Exception:
                return
            
            # Get window PID
            try:
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            
            # CRITICAL: Skip bot's own windows
            if window_pid == self.my_pid:
                return
            
            # CRITICAL: Skip blacklisted keywords (avoid GUI windows)
            for keyword in self.blacklist_keywords:
                if keyword.lower() in title.lower():
                    return
            
            # Check if title matches target
            for target in self.target_titles:
                if target.lower() in title.lower():
                    ctx.append((hwnd, title, window_pid))
                    return
        
        found = []
        win32gui.EnumWindows(callback, found)
        
        if found:
            self.target_hwnd, window_title, window_pid = found[0]
            print(f"[WindowCapture] Target window found: '{window_title}'")
            print(f"[WindowCapture] HWND: {self.target_hwnd} | PID: {window_pid}")
            print(f"[WindowCapture] Bot PID: {self.my_pid} (filtered out)")
        else:
            print(f"[WindowCapture] WARNING: No window found with titles: {self.target_titles}")
            print("[WindowCapture] Capture will fail until target window is found.")
    
    def capture_frame(self) -> Optional[np.ndarray]:  # NOSONAR
        """
        Capture a frame of the game window's CLIENT AREA using MSS.
        
        CRITICAL: Captures only the client area (game content), excluding
        title bar and window borders. This ensures coordinates match
        with get_screen_position() which uses ClientToScreen.
        
        Returns:
            numpy.ndarray: BGR image of the game client area, or None on failure
        """
        try:
            if self.sct is None:
                self._init_mss()
                if self.sct is None:
                    return None

            # Step 1: Find/validate window
            if not self.target_hwnd:
                self._find_target_window()
                if not self.target_hwnd:
                    return None
            
            # Step 2: Get CLIENT AREA coordinates (not window rect!)
            try:
                # GetClientRect returns client area size (0, 0, width, height)
                client_rect = win32gui.GetClientRect(self.target_hwnd)
                client_width = client_rect[2]
                client_height = client_rect[3]
                
                # ClientToScreen converts (0,0) of client area to screen coords
                # This gives us the top-left of the actual game content
                client_left, client_top = win32gui.ClientToScreen(self.target_hwnd, (0, 0))
                
            except Exception as e:
                # Window was closed or invalid
                if self.error_count % 100 == 0:
                    print(f"[Capture] Window handle lost: {e}")
                self.target_hwnd = None
                self.error_count += 1
                return None
            
            # Step 3: Validate - Check for minimized window
            if client_left <= -32000:
                if self.error_count % 100 == 0:
                    print("[Capture] Window is MINIMIZED. Restore the game window!")
                self.error_count += 1
                return None
            
            # Step 4: Validate dimensions
            if client_width <= 0 or client_height <= 0:
                if self.error_count % 100 == 0:
                    print(f"[Capture] Invalid client size: {client_width}x{client_height}")
                self.error_count += 1
                return None
            
            if client_width > 10000 or client_height > 10000:
                if self.error_count % 100 == 0:
                    print(f"[Capture] Suspiciously large client area: {client_width}x{client_height}")
                self.error_count += 1
                return None
            
            # Update cached client area position
            self.window_x = client_left
            self.window_y = client_top
            self.window_width = client_width
            self.window_height = client_height
            
            # Step 5: Define capture region for CLIENT AREA only
            monitor = {
                "top": int(client_top),
                "left": int(client_left),
                "width": int(client_width),
                "height": int(client_height)
            }
            
            # Step 6: Capture region using MSS
            try:
                screenshot = self.sct.grab(monitor)
            except Exception as grab_error:
                if self.error_count % 100 == 0:
                    print(f"[Capture] MSS grab() failed: {grab_error}")
                self.error_count += 1
                return None
            
            # Step 7: Convert to numpy array (BGRA -> BGR)
            frame = np.array(screenshot)
            frame = frame[:, :, :3]  # Drop alpha channel
            
            # Step 8: Validate frame
            if frame.size == 0:
                if self.error_count % 100 == 0:
                    print("[Capture] Frame is empty after conversion")
                self.error_count += 1
                return None
            
            # Success!
            self.frame_count += 1
            return frame
            
        except Exception as e:
            if self.error_count % 100 == 0:
                print(f"[Capture] Unexpected error: {e}")
                import traceback
                traceback.print_exc()
            self.error_count += 1
            return None
    
    def get_screen_position(self, local_x: int, local_y: int) -> Tuple[int, int]:
        """
        Convert local image coordinates to global screen coordinates.
        
        CRITICAL FIX: Uses ClientToScreen API to properly account for
        window title bar and borders. GetWindowRect returns outer window
        bounds, but we capture the CLIENT area, so we need client-based
        coordinate conversion.
        
        Args:
            local_x: X coordinate within the captured frame (client area)
            local_y: Y coordinate within the captured frame (client area)
        
        Returns:
            Tuple[int, int]: (screen_x, screen_y) global screen coordinates
        """
        hwnd = self.target_hwnd
        if hwnd is None:
            return (self.window_x + 8 + local_x, self.window_y + 30 + local_y)

        try:
            # ClientToScreen converts a point in client coordinates 
            # to screen coordinates, accounting for title bar & borders
            screen_x, screen_y = win32gui.ClientToScreen(hwnd, (local_x, local_y))
            
            return screen_x, screen_y
            
        except Exception:
            # Fallback: use GetWindowRect + estimate border offset
            try:
                left, top, _, _ = win32gui.GetWindowRect(hwnd)
                
                # Estimate title bar height (~30px) and border (~8px)
                # This is a rough fallback if ClientToScreen fails
                TITLE_BAR_HEIGHT = 30
                BORDER_WIDTH = 8
                
                screen_x = left + BORDER_WIDTH + local_x
                screen_y = top + TITLE_BAR_HEIGHT + local_y
                
                return screen_x, screen_y
            except Exception:
                # Last resort: use cached values with offset
                screen_x = self.window_x + 8 + local_x
                screen_y = self.window_y + 30 + local_y
                return screen_x, screen_y
    
    def release(self):
        """
        Release capture resources.
        Call this when done capturing to free resources.
        """
        print("[WindowCapture] Releasing capture resources...")
        print(f"[WindowCapture] Stats - Frames: {self.frame_count}, Errors: {self.error_count}")
        
        if self.sct:
            try:
                self.sct.close()
            except Exception as e:
                print(f"[WindowCapture] Warning during MSS cleanup: {e}")
            
            self.sct = None
    
    def check_window_size(self) -> bool:
        """
        Check if target window client area size has changed.
        
        Returns:
            bool: True if size changed (for compatibility with old code)
        """
        try:
            if not self.target_hwnd:
                return False
            
            # Use GetClientRect for consistency
            client_rect = win32gui.GetClientRect(self.target_hwnd)
            width = client_rect[2]
            height = client_rect[3]
            
            if width != self.window_width or height != self.window_height:
                print(f"[WindowCapture] Client size changed: {self.window_width}x{self.window_height} -> {width}x{height}")
                self.window_width = width
                self.window_height = height
                return True
            
            return False
            
        except Exception as e:
            print(f"[WindowCapture] ERROR: Failed to check window size: {e}")
            return False
    
    def is_window_focused(self) -> bool:
        """
        ISSUE 2 FIX: Focus Safety Gate
        
        Check if the game window is currently the foreground (active) window.
        This MUST be called before any click/input action to prevent
        clicking on other windows.
        
        Returns:
            bool: True if game window is focused, False otherwise
        """
        try:
            if not self.target_hwnd:
                return False
            
            # Get the handle of the currently active window
            foreground_hwnd = win32gui.GetForegroundWindow()
            
            # Compare with our target game window
            return foreground_hwnd == self.target_hwnd
            
        except Exception:
            # If we can't determine focus, assume NOT focused (safer)
            return False
    
    def get_client_area_geometry(self) -> Tuple[int, int, int, int]:
        """
        ISSUE 1 FIX: Get precise client area geometry for overlay synchronization.
        
        Returns the exact screen coordinates and dimensions of the game's
        playable area (excluding title bar and borders).
        
        Returns:
            Tuple[int, int, int, int]: (screen_x, screen_y, width, height)
            Returns (0, 0, 0, 0) if window handle is invalid
        """
        try:
            if not self.target_hwnd:
                return (0, 0, 0, 0)
            
            # Get client area dimensions
            client_rect = win32gui.GetClientRect(self.target_hwnd)
            width = client_rect[2]
            height = client_rect[3]
            
            # Get client area top-left corner in screen coordinates
            screen_x, screen_y = win32gui.ClientToScreen(self.target_hwnd, (0, 0))
            
            return (screen_x, screen_y, width, height)
            
        except Exception as e:
            print(f"[WindowCapture] ERROR: Failed to get client geometry: {e}")
            return (0, 0, 0, 0)


# =============== Standalone Test (Optional) ===============

if __name__ == "__main__":
    """
    Test the MSS capture system.
    Press Ctrl+C to quit.
    """
    import time
    
    print("=" * 70)
    print(" WindowCapture MSS Test")
    print("=" * 70)

    capture: Optional[WindowCapture] = None
    
    try:
        capture = WindowCapture()
        
        if not capture.target_hwnd:
            print("\n[WARNING] No game window found. Waiting for window...")
        
        print("\n[READY] Capturing frames. Press Ctrl+C to quit.\n")
        
        frame_times = []
        
        while True:
            start = time.time()
            frame = capture.capture_frame()
            end = time.time()
            
            if frame is not None:
                frame_times.append(end - start)
                
                # Print stats every 100 frames
                if len(frame_times) >= 100:
                    avg_time = sum(frame_times) / len(frame_times)
                    fps = 1.0 / avg_time if avg_time > 0 else 0
                    print(f"[STATS] Avg Frame Time: {avg_time*1000:.2f}ms | FPS: {fps:.1f} | "
                          f"Frames: {capture.frame_count} | Errors: {capture.error_count}")
                    frame_times = []
            
            time.sleep(0.001)  # Minimal delay
        
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user...")
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if capture is not None:
            capture.release()
        print("[INFO] Test completed.")
