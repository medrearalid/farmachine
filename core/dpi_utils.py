"""
DPI Utilities - Resolution Independence Module
===============================================
This module provides system-wide DPI awareness and coordinate transformation
utilities to ensure the bot works correctly on any display (1080p, 2K, 4K)
and any Windows Scaling setting (100%, 125%, 150%, 200%).

Architecture:
- SetProcessDpiAwareness(2) = Per-Monitor DPI Aware (best)
- SetProcessDpiAwareness(1) = System DPI Aware (fallback)
- SetProcessDPIAware() = Legacy fallback

CRITICAL: This module must be imported BEFORE any GUI initialization!
"""

import ctypes
import ctypes.wintypes
from typing import Tuple, Optional
import os


# ============================================================================
# DPI AWARENESS - Must be called at process startup BEFORE any GUI
# ============================================================================

_dpi_awareness_set = False
_cached_dpi_scale = None


def set_process_dpi_awareness() -> bool:
    """
    Force DPI awareness for the current process.
    
    This MUST be called before creating any windows or GUI elements.
    It instructs Windows to:
    1. Report PHYSICAL pixels from GetWindowRect/GetClientRect
    2. Disable "Virtual Scaling" (DWM virtualization)
    3. Allow proper coordinate transformation
    
    DPI Awareness Levels:
    - 0 = DPI_AWARENESS_UNAWARE (Windows scales everything - BAD)
    - 1 = SYSTEM_DPI_AWARE (Primary monitor DPI only)
    - 2 = PER_MONITOR_DPI_AWARE (Best - each monitor handled correctly)
    
    Returns:
        bool: True if successfully set, False otherwise
    """
    global _dpi_awareness_set
    
    if _dpi_awareness_set:
        return True
    
    try:
        # Method 1: Windows 8.1+ SetProcessDpiAwareness (Best)
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
            if result == 0:  # S_OK
                _dpi_awareness_set = True
                print("[DPI] Per-Monitor DPI Awareness enabled (Level 2)")
                return True
        except AttributeError:
            pass  # shcore not available (Windows 7)
        except Exception as e:
            # E_ACCESSDENIED (0x80070005) = Already set by manifest/Qt
            winerror = getattr(e, 'winerror', None)
            if winerror == 0x80070005:
                _dpi_awareness_set = True
                print("[DPI] DPI Awareness already set by system")
                return True
        
        # Method 2: Fallback to System DPI Aware
        try:
            result = ctypes.windll.shcore.SetProcessDpiAwareness(1)
            if result == 0:
                _dpi_awareness_set = True
                print("[DPI] System DPI Awareness enabled (Level 1)")
                return True
        except:
            pass
        
        # Method 3: Legacy Windows 7 fallback
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            _dpi_awareness_set = True
            print("[DPI] Legacy DPI Awareness enabled (SetProcessDPIAware)")
            return True
        except:
            pass
        
        print("[DPI] WARNING: Failed to set DPI awareness!")
        return False
        
    except Exception as e:
        print(f"[DPI] ERROR: DPI awareness setup failed: {e}")
        return False


def get_dpi_scale_factor() -> float:
    """
    Get the current Windows DPI scale factor.
    
    Windows DPI Scaling Reference:
    - 100% = 96 DPI = scale factor 1.0
    - 125% = 120 DPI = scale factor 1.25
    - 150% = 144 DPI = scale factor 1.5
    - 175% = 168 DPI = scale factor 1.75
    - 200% = 192 DPI = scale factor 2.0
    
    Returns:
        float: The DPI scale factor (e.g., 1.25 for 125% scaling)
    """
    global _cached_dpi_scale
    
    if _cached_dpi_scale is not None:
        return _cached_dpi_scale
    
    try:
        user32 = ctypes.windll.user32
        
        # Method 1: GetDpiForSystem (Windows 10 1607+) - Most accurate
        try:
            dpi = user32.GetDpiForSystem()
            if dpi > 0:
                _cached_dpi_scale = dpi / 96.0
                print(f"[DPI] Detected DPI: {dpi} (Scale: {_cached_dpi_scale:.2f}x = {int(_cached_dpi_scale * 100)}%)")
                return _cached_dpi_scale
        except AttributeError:
            pass
        
        # Method 2: GetDeviceCaps with HDC (Works on all Windows)
        try:
            hdc = user32.GetDC(0)
            if hdc:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                user32.ReleaseDC(0, hdc)
                if dpi > 0:
                    _cached_dpi_scale = dpi / 96.0
                    print(f"[DPI] Detected DPI (via HDC): {dpi} (Scale: {_cached_dpi_scale:.2f}x)")
                    return _cached_dpi_scale
        except:
            pass
        
        # Method 3: Query registry (last resort)
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                               r"Control Panel\Desktop\WindowMetrics") as key:
                applied_dpi = winreg.QueryValueEx(key, "AppliedDPI")[0]
                if applied_dpi > 0:
                    _cached_dpi_scale = applied_dpi / 96.0
                    print(f"[DPI] Detected DPI (via Registry): {applied_dpi}")
                    return _cached_dpi_scale
        except:
            pass
        
        # Fallback: Assume 100% scaling
        print("[DPI] WARNING: Could not detect DPI, assuming 100% (96 DPI)")
        _cached_dpi_scale = 1.0
        return 1.0
        
    except Exception as e:
        print(f"[DPI] ERROR: DPI detection failed: {e}")
        _cached_dpi_scale = 1.0
        return 1.0


def get_dpi_for_window(hwnd: int) -> int:
    """
    Get the DPI for a specific window (Per-Monitor aware).
    
    Args:
        hwnd: Window handle
        
    Returns:
        int: DPI value (96, 120, 144, 192, etc.)
    """
    try:
        # GetDpiForWindow is Windows 10 1607+
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi > 0:
            return dpi
    except:
        pass
    
    # Fallback to system DPI
    return int(get_dpi_scale_factor() * 96)


# ============================================================================
# COORDINATE TRANSFORMATION - Client-Space Logic
# ============================================================================

def client_to_screen(hwnd: int, local_x: int, local_y: int) -> Tuple[int, int]:
    """
    Convert local client-area coordinates to global screen coordinates.
    
    This is the PROPER way to convert YOLO detection coordinates to click positions.
    
    Args:
        hwnd: Window handle
        local_x: X coordinate relative to client area (0,0 = top-left of game content)
        local_y: Y coordinate relative to client area
        
    Returns:
        Tuple[int, int]: (screen_x, screen_y) global screen coordinates
    """
    try:
        import win32gui
        
        # ClientToScreen converts a point in the client area to screen coordinates
        # This AUTOMATICALLY accounts for:
        # - Title bar height
        # - Window borders
        # - Window position on screen
        screen_x, screen_y = win32gui.ClientToScreen(hwnd, (local_x, local_y))
        return (screen_x, screen_y)
        
    except Exception as e:
        print(f"[DPI] ERROR: ClientToScreen failed: {e}")
        # Return input as fallback (may be incorrect)
        return (local_x, local_y)


def get_client_area_geometry(hwnd: int) -> Tuple[int, int, int, int]:
    """
    Get the client area geometry (excluding title bar and borders).
    
    This returns PHYSICAL PIXELS regardless of DPI scaling,
    thanks to SetProcessDpiAwareness(2).
    
    Args:
        hwnd: Window handle
        
    Returns:
        Tuple[int, int, int, int]: (screen_x, screen_y, width, height)
        Returns (0, 0, 0, 0) if window handle is invalid
    """
    try:
        import win32gui
        
        # GetClientRect returns (0, 0, width, height) - dimensions only
        client_rect = win32gui.GetClientRect(hwnd)
        width = client_rect[2]
        height = client_rect[3]
        
        # ClientToScreen converts (0, 0) to screen coordinates
        screen_x, screen_y = win32gui.ClientToScreen(hwnd, (0, 0))
        
        return (screen_x, screen_y, width, height)
        
    except Exception as e:
        print(f"[DPI] ERROR: get_client_area_geometry failed: {e}")
        return (0, 0, 0, 0)


# ============================================================================
# RELATIVE COORDINATE CALCULATION
# ============================================================================

def calculate_relative_position(
    client_width: int, 
    client_height: int,
    rel_x: float,
    rel_y: float
) -> Tuple[int, int]:
    """
    Calculate pixel position from relative coordinates (0.0 to 1.0).
    
    This is the PREFERRED method for resolution-independent positioning.
    
    Example:
        - (0.5, 0.5) = Center of window
        - (0.0, 0.0) = Top-left corner
        - (1.0, 1.0) = Bottom-right corner
        - (0.127, 0.143) = Revive button at ~14% from left, ~14% from top
    
    Args:
        client_width: Client area width in pixels
        client_height: Client area height in pixels
        rel_x: Relative X position (0.0 = left, 1.0 = right)
        rel_y: Relative Y position (0.0 = top, 1.0 = bottom)
        
    Returns:
        Tuple[int, int]: (pixel_x, pixel_y) in client coordinates
    """
    pixel_x = int(client_width * rel_x)
    pixel_y = int(client_height * rel_y)
    return (pixel_x, pixel_y)


def calculate_relative_rect(
    client_width: int,
    client_height: int,
    rel_x: float,
    rel_y: float,
    rel_width: float,
    rel_height: float
) -> Tuple[int, int, int, int]:
    """
    Calculate pixel rectangle from relative coordinates.
    
    Args:
        client_width: Client area width in pixels
        client_height: Client area height in pixels
        rel_x: Relative left position (0.0 to 1.0)
        rel_y: Relative top position (0.0 to 1.0)
        rel_width: Relative width (0.0 to 1.0)
        rel_height: Relative height (0.0 to 1.0)
        
    Returns:
        Tuple[int, int, int, int]: (x, y, width, height) in pixels
    """
    x = int(client_width * rel_x)
    y = int(client_height * rel_y)
    width = int(client_width * rel_width)
    height = int(client_height * rel_height)
    return (x, y, width, height)


# ============================================================================
# UI ELEMENT CONSTANTS (Relative Coordinates)
# ============================================================================
# All positions are expressed as ratios (0.0 to 1.0) of the client area
# This makes them resolution-independent

class RelativeUI:
    """
    Resolution-independent UI element positions.
    
    All values are ratios of the client area dimensions:
    - X values: 0.0 = left edge, 1.0 = right edge
    - Y values: 0.0 = top edge, 1.0 = bottom edge
    """
    
    # Player anchor point (where player character appears on screen)
    # Used for distance calculations to metin stones
    PLAYER_ANCHOR_X = 0.5    # Center of screen
    PLAYER_ANCHOR_Y = 0.6    # 60% down from top
    
    # "Restart Here" button (Burada yeniden başla)
    # Appears after death in the top-left area
    # Based on 1024x768: (130, 115) -> (0.127, 0.150)
    RESTART_HERE_X = 0.127   # ~12.7% from left
    RESTART_HERE_Y = 0.150   # ~15% from top
    
    # Target HP Bar region (top-center of screen)
    HP_BAR_ROI_X = 0.35      # Start at 35% from left
    HP_BAR_ROI_Y = 0.0       # Start at top
    HP_BAR_ROI_WIDTH = 0.30  # 30% of screen width
    HP_BAR_ROI_HEIGHT = 0.15 # 15% of screen height
    
    # Edge exclusion margin (for target selection)
    EDGE_MARGIN = 0.05       # 5% of screen as border margin
    
    # Minimap exclusion zone (top-right corner)
    MINIMAP_X = 0.82         # Right 18% of screen
    MINIMAP_Y = 0.0          # Top
    MINIMAP_WIDTH = 0.18
    MINIMAP_HEIGHT = 0.25
    
    # Bottom UI bar exclusion
    BOTTOM_UI_Y = 0.90       # Bottom 10% of screen
    
    # Left UI panel exclusion
    LEFT_UI_WIDTH = 0.05     # Left 5% of screen
    
    # Top-Left status panel exclusion  
    STATUS_PANEL_WIDTH = 0.35
    STATUS_PANEL_HEIGHT = 0.15


# ============================================================================
# MODULE INITIALIZATION
# ============================================================================

# Automatically set DPI awareness when module is imported
# This should happen before any other imports that might create windows
set_process_dpi_awareness()
