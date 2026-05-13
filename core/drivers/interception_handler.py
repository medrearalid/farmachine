"""
Interception Mouse Auto-Detection Handler
Provides automatic mouse ID detection with timeout and movement-based identification.
"""

import ctypes
import sys
import os
import time
from typing import Optional

# Get the directory containing this script
script_dir = os.path.dirname(os.path.abspath(__file__))
dll_path = os.path.join(script_dir, "interception.dll")

# Add script directory to PATH so dependent DLLs are found
os.add_dll_directory(script_dir)

# Load the DLL
try:
    interception = ctypes.CDLL(dll_path)
except OSError as e:
    print(f"Error loading interception.dll: {e}", file=sys.stderr)
    sys.exit(1)

# Define constants
INTERCEPTION_MOUSE_MOVE_RELATIVE = 0x000
INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN = 0x001

# Define structures
class InterceptionMouseStroke(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_ushort),
        ("flags", ctypes.c_ushort),
        ("rolling", ctypes.c_short),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("information", ctypes.c_uint)
    ]

class InterceptionKeyStroke(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("state", ctypes.c_ushort),
        ("information", ctypes.c_uint)
    ]

# Define function signatures
interception.interception_create_context.restype = ctypes.c_void_p
interception.interception_create_context.argtypes = []

interception.interception_destroy_context.restype = None
interception.interception_destroy_context.argtypes = [ctypes.c_void_p]

# Define predicate type
InterceptionPredicate = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)

interception.interception_set_filter.restype = None
interception.interception_set_filter.argtypes = [ctypes.c_void_p, InterceptionPredicate, ctypes.c_ushort]

interception.interception_wait_with_timeout.restype = ctypes.c_int
interception.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

interception.interception_receive.restype = ctypes.c_int
interception.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]

interception.interception_send.restype = ctypes.c_int
interception.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]

interception.interception_is_mouse.restype = ctypes.c_int
interception.interception_is_mouse.argtypes = [ctypes.c_int]

interception.interception_is_keyboard.restype = ctypes.c_int
interception.interception_is_keyboard.argtypes = [ctypes.c_int]


def find_active_mouse_silent() -> Optional[int]:
    """
    Non-blocking mouse discovery.

    Scans Interception device IDs 1..10 and returns the first device that is
    currently recognized as a mouse by `interception_is_mouse`.

    Returns:
        int: First active mouse device ID
        None: If no active mouse is detected
    """
    try:
        # Interception canonical mouse IDs are usually 11..20.
        for device_id in range(11, 21):
            if interception.interception_is_mouse(device_id):
                return device_id

        # Fallback scan for environments with non-standard mapping.
        for device_id in range(1, 21):
            if interception.interception_is_mouse(device_id):
                return device_id
    except Exception:
        # Keep this helper fully silent and non-blocking for runtime recovery.
        return None
    return None


def auto_detect_mouse_id(timeout_seconds: int = 10, callback=None) -> Optional[int]:  # NOSONAR
    """
    Auto-detect mouse device ID by waiting for mouse movement.
    
    This function creates an Interception context, waits for the user to move
    the mouse, captures the device ID, and returns it.
    
    Args:
        timeout_seconds: Maximum time to wait (default 10)
        callback: Optional callback function to report progress (e.g., for GUI updates)
    
    Returns:
        int: Mouse device ID if found, None if timeout or error
    """
    context = interception.interception_create_context()
    if not context:
        if callback:
            callback("error", "Failed to create interception context")
        return None

    # Define mouse filter predicate
    @InterceptionPredicate
    def is_mouse_wrapper(device):
        return interception.interception_is_mouse(device)

    # Set filter to capture ALL mouse events (0xFFFF = all events)
    interception.interception_set_filter(context, is_mouse_wrapper, ctypes.c_ushort(0xFFFF))

    if callback:
        callback("waiting", "Please move your mouse...")
    
    found_device = None
    stroke = InterceptionMouseStroke()
    start_time = time.time()
    
    try:
        while True:
            # Check timeout
            if time.time() - start_time > timeout_seconds:
                if callback:
                    callback("timeout", "Timeout reached. No mouse movement detected.")
                break
            
            # Wait for device event with 100ms timeout
            device = interception.interception_wait_with_timeout(context, 100)
            
            if device == 0:
                # Timeout on wait, continue loop to check global timeout
                continue
            
            if interception.interception_is_mouse(device):
                # Receive the event
                if interception.interception_receive(context, device, ctypes.byref(stroke), 1) > 0:
                    # Check for movement (x or y != 0)
                    if stroke.x != 0 or stroke.y != 0:
                        found_device = device
                        if callback:
                            callback("found", f"Mouse detected! Device ID: {device}")
                        
                        # Forward the movement stroke so user doesn't notice lag
                        interception.interception_send(context, device, ctypes.byref(stroke), 1)
                        break
                    
                    # Forward other mouse events
                    interception.interception_send(context, device, ctypes.byref(stroke), 1)

    except Exception as e:
        if callback:
            callback("error", f"Error during detection: {e}")
    
    finally:
        # CRITICAL: Always destroy context to prevent driver lock
        interception.interception_destroy_context(context)
        
    return found_device


def find_mouse_id_by_click() -> Optional[int]:
    """
    Legacy method: Find mouse ID via left click (kept for backward compatibility).
    
    Returns:
        int: Mouse device ID if found, None otherwise
    """
    context = interception.interception_create_context()
    if not context:
        print("Failed to create interception context")
        return None

    @InterceptionPredicate
    def is_mouse_wrapper(device):
        return interception.interception_is_mouse(device)

    interception.interception_set_filter(context, is_mouse_wrapper, ctypes.c_ushort(0xFFFF))

    print("Please LEFT CLICK your mouse to identify it...")
    
    found_device = None
    stroke = InterceptionMouseStroke()
    
    try:
        while True:
            device = interception.interception_wait_with_timeout(context, 10000)  # 10s timeout
            
            if device == 0:
                print("Timeout waiting for click.")
                break
            
            if interception.interception_is_mouse(device):
                if interception.interception_receive(context, device, ctypes.byref(stroke), 1) > 0:
                    # Check for left click down
                    if stroke.state & INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN:
                        print(f"Click detected from Device ID: {device}")
                        found_device = device
                        # Forward the click
                        interception.interception_send(context, device, ctypes.byref(stroke), 1)
                        break
                    
                    # Forward other events
                    interception.interception_send(context, device, ctypes.byref(stroke), 1)

    finally:
        interception.interception_destroy_context(context)
        
    return found_device


if __name__ == "__main__":
    def progress_callback(status, message):
        print(f"[{status.upper()}] {message}")
    
    print("=== Mouse Auto-Detection ===")
    print("Method: Movement-based detection")
    print("Timeout: 10 seconds\n")
    
    mouse_id = auto_detect_mouse_id(timeout_seconds=10, callback=progress_callback)
    
    if mouse_id:
        print(f"\n✓ SUCCESS: FOUND_MOUSE_ID={mouse_id}")
        
        # Save to config
        try:
            import json
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json")
            
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)

                if "global" in config and isinstance(config.get("global"), dict):
                    config.setdefault("global", {})["mouse_id"] = int(mouse_id)
                elif "system" in config and isinstance(config.get("system"), dict):
                    config.setdefault("system", {})["mouse_id"] = str(mouse_id)
                else:
                    config["global"] = {"mouse_id": int(mouse_id)}
                
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4)
                
                print("✓ Saved to config.json")
        except Exception as e:
            print(f"Warning: Could not save to config.json: {e}")
    else:
        print("\n✗ FAILED: Mouse ID not detected")
        sys.exit(1)
