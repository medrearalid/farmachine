"""
Windows Window Manager Module
Handles High-DPI scaling awareness and game window detection.

Resolution Independence:
- All UI areas use RELATIVE coordinates (0.0 to 1.0)
- Actual pixel values calculated at runtime based on window size
- Works on 1080p, 2K, 4K, and any Windows scaling (100%-200%)
"""

import ctypes
import win32gui
import win32api
import win32con
from typing import Dict, Tuple, List

# Import DPI utilities for proper coordinate handling
try:
    from core.dpi_utils import (
        set_process_dpi_awareness,
        get_dpi_scale_factor,
        RelativeUI,
        calculate_relative_rect
    )
except ImportError:
    # Fallback if dpi_utils not available
    pass

# ============================================================================
# RESTRICTED AREAS DEFINITION (Resolution-Independent)
# ============================================================================
# All values are RELATIVE to client area (0.0 to 1.0)
# Format: {'rel_x': float, 'rel_y': float, 'rel_width': float, 'rel_height': float}

RESTRICTED_AREAS_RELATIVE: List[Dict[str, float]] = [
    # Top-Left UI (Character Status, HP/MP, Level, Equipment Info)
    # Original 1024x768: (0, 0, 350, 120) -> (0, 0, 0.34, 0.16)
    {'rel_x': 0.0, 'rel_y': 0.0, 'rel_width': 0.34, 'rel_height': 0.16},
    
    # Top-Right UI (Minimap, Chat Window)
    # Original 1024x768: (750, 0, 270, 150) -> (0.73, 0, 0.27, 0.20)
    {'rel_x': 0.73, 'rel_y': 0.0, 'rel_width': 0.27, 'rel_height': 0.20},
    
    # Bottom UI Bar (Skill Bar, Quick Slots, Inventory, Experience Bar)
    # Original 1024x768: (0, 680, 1024, 80) -> (0, 0.89, 1.0, 0.11)
    {'rel_x': 0.0, 'rel_y': 0.89, 'rel_width': 1.0, 'rel_height': 0.11},
    
    # Left Side Panel (Quest Log, Buffs/Status Effects) - if present
    # Original 1024x768: (0, 120, 50, 560) -> (0, 0.16, 0.05, 0.73)
    {'rel_x': 0.0, 'rel_y': 0.16, 'rel_width': 0.05, 'rel_height': 0.73},
    
    # Right Side Panel (Chat/Party Info) - if present
    # Original 1024x768: (970, 150, 50, 530) -> (0.95, 0.20, 0.05, 0.69)
    {'rel_x': 0.95, 'rel_y': 0.20, 'rel_width': 0.05, 'rel_height': 0.69},
]


def get_restricted_areas_for_resolution(width: int, height: int) -> List[Dict[str, int]]:
    """
    Calculate restricted areas in pixels for a given resolution.
    
    Args:
        width: Client area width in pixels
        height: Client area height in pixels
        
    Returns:
        List of restricted areas in pixel coordinates
    """
    result = []
    for area in RESTRICTED_AREAS_RELATIVE:
        result.append({
            'x': int(width * area['rel_x']),
            'y': int(height * area['rel_y']),
            'width': int(width * area['rel_width']),
            'height': int(height * area['rel_height']),
        })
    return result


# Legacy compatibility: Generate pixel-based areas for 1024x768 as default
RESTRICTED_AREAS: List[Dict[str, int]] = get_restricted_areas_for_resolution(1024, 768)

def set_dpi_awareness():
    """
    Force DPI awareness to fix 125% scaling offset issue on high-DPI displays.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception as e:
        pass

def get_game_region(window_title: str) -> Dict[str, int]:
    """
    Find a game window by title (partial match) and return its region compatible with MSS.
    """
    hwnd = win32gui.FindWindow(None, window_title)
    
    if hwnd == 0:
        import ctypes
        hwnds = []
        def callback(h, ctx):
            hwnds.append(h)
            return True
        win32gui.EnumWindows(callback, None)
        for h in hwnds:
            if win32gui.IsWindowVisible(h):
                title = win32gui.GetWindowText(h)
                if window_title in title:
                    hwnd = h
                    break
    
    if hwnd == 0:
        raise Exception(f"Game Window Not Found: '{window_title}'")
    
    if win32gui.IsIconic(hwnd):
        raise Exception(f"Game Window '{window_title}' is minimized. Please restore it.")
    
    try:
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        
        region = {
            'top': top,
            'left': left,
            'width': width,
            'height': height
        }
        return region
    except Exception as e:
        raise Exception(f"Error getting window region: {e}")

def get_target_bar_region(game_region: Dict[str, int]) -> Dict[str, int]:
    """
    Calculate the Region of Interest (ROI) for the Target HP Bar.
    In Metin2, the Target HP Bar ALWAYS appears at the **Top-Center** of the screen.
    
    RESOLUTION INDEPENDENCE:
    - ROI dimensions are calculated as percentages of window size
    - Works correctly on 1080p, 2K, 4K, and any scaling
    
    Args:
        game_region: The full game window region {'left': x, 'top': y, 'width': w, 'height': h}
    
    Returns:
        A dictionary {'top': roi_top, 'left': roi_left, 'width': roi_width, 'height': roi_height}
        representing the sub-region where the Target HP Bar is located.
    """
    client_width = game_region['width']
    client_height = game_region['height']
    
    # ROI dimensions as percentages of client area
    # ~60% width centered, ~20% height from top
    ROI_WIDTH_RATIO = 0.60    # 60% of window width
    ROI_HEIGHT_RATIO = 0.20   # 20% of window height
    TOP_OFFSET_RATIO = 0.025  # 2.5% offset from top (skip title bar area)
    
    roi_width = int(client_width * ROI_WIDTH_RATIO)
    roi_height = int(client_height * ROI_HEIGHT_RATIO)
    
    # Center horizontally within the game window
    center_x = game_region['left'] + client_width // 2
    roi_left = center_x - roi_width // 2
    
    # Position with a small relative offset from the top
    roi_top = game_region['top'] + int(client_height * TOP_OFFSET_RATIO)
    
    return {
        'top': roi_top,
        'left': roi_left,
        'width': roi_width,
        'height': roi_height
    }

def convert_local_to_global(region: Dict[str, int], local_x: int, local_y: int) -> Tuple[int, int]:
    """Convert coordinates from local (within screenshot) to global (screen) coordinates."""
    global_x = region['left'] + local_x
    global_y = region['top'] + local_y
    return (global_x, global_y)

def is_point_in_restricted_area(point_x: int, point_y: int, game_region: Dict[str, int]) -> bool:
    """
    Check if a given point (in global screen coordinates) falls within any restricted area.
    
    RESOLUTION INDEPENDENCE:
    - Restricted areas are calculated dynamically based on current window size
    - Works correctly on any resolution and DPI scaling
    
    Args:
        point_x: Global screen X coordinate
        point_y: Global screen Y coordinate
        game_region: Game window region dict
        
    Returns:
        True if point is in a restricted UI area, False otherwise
    """
    game_left = game_region['left']
    game_top = game_region['top']
    client_width = game_region['width']
    client_height = game_region['height']
    
    # Calculate restricted areas dynamically based on current resolution
    current_restricted_areas = get_restricted_areas_for_resolution(client_width, client_height)
    
    for area in current_restricted_areas:
        area_global_left = game_left + area['x']
        area_global_top = game_top + area['y']
        area_global_right = area_global_left + area['width']
        area_global_bottom = area_global_top + area['height']
        
        if (area_global_left <= point_x <= area_global_right and
            area_global_top <= point_y <= area_global_bottom):
            return True
    return False


def bring_window_to_front(window_title: str) -> bool:
    """
    OBJECTIVE 1: Auto-focus the game window.
    Find window by title and bring it to foreground.
    
    Args:
        window_title: The title (or partial title) of the window to focus.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        # Try exact match first
        hwnd = win32gui.FindWindow(None, window_title)
        
        # If not found, try partial match
        if hwnd == 0:
            hwnds = []
            def callback(h, ctx):
                hwnds.append(h)
                return True
            win32gui.EnumWindows(callback, None)
            for h in hwnds:
                if win32gui.IsWindowVisible(h):
                    title = win32gui.GetWindowText(h)
                    if window_title in title:
                        hwnd = h
                        break
        
        if hwnd == 0:
            return False
        
        # Restore if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        
        # Bring to foreground
        win32gui.SetForegroundWindow(hwnd)
        return True
        
    except Exception as e:
        return False


def bring_window_to_front_by_hwnd(hwnd: int) -> bool:
    """
    Bring a specific window to foreground by HWND.

    Args:
        hwnd: Target window handle.

    Returns:
        True if successful, False otherwise.
    """
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False

# CRITICAL: Set DPI awareness immediately when module loads
set_dpi_awareness()
