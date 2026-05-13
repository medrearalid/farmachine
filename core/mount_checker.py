"""
Mount Checker - Template Matching Based
========================================
Detects if the character is MOUNTED or UNMOUNTED by pressing F1 (horse skill)
and looking for the "I need a Horse" warning text.

Algorithm:
1. Press F1 (horse skill key)
2. Wait 0.3s for game UI to render
3. Capture central screen area
4. Template match for "text_need_horse.png"
5. If match > 0.8 threshold: UNMOUNTED (warning visible)
6. Else: MOUNTED (no warning, skill activated successfully)

Uses: cv2.matchTemplate with TM_CCOEFF_NORMED (no HSV filtering)

DEBUG MODE:
- Saves captured screenshot to debug_mount_capture.png
- Saves THRESHOLDED view to debug_processed_view.png (white text on black)
- Saves result with match rectangle to debug_mount_result.png
- Prints confidence scores to console

BINARY THRESHOLD MATCHING:
- Converts both template and screenshot to grayscale
- Applies binary threshold (pixels > 200 = white, else = black)
- Matches "Pure White Letters on Black" vs "Pure White Letters on Black"
- Background noise (grass, soil) becomes black (0) and is ignored
- Result: 0.95+ confidence for correct text, ~0.0 for wrong text
"""

import os
import time
import logging
import cv2
import numpy as np
from typing import Optional, Tuple

# Try importing pydirectinput for F1 press
pydirectinput = None
try:
    import pydirectinput
    HAS_PYDIRECTINPUT = True
except ImportError:
    HAS_PYDIRECTINPUT = False

# Try importing mss for screen capture
mss = None
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

logger = logging.getLogger(__name__)


class MountChecker:
    """
    Detects mount status using template matching.
    
    Press F1 -> If "I need a Horse" text appears -> UNMOUNTED
    Otherwise -> MOUNTED
    """
    
    # Template matching threshold (0.85 = 85% confidence required)
    # With binary thresholding, we expect 0.95+ for real matches
    MATCH_THRESHOLD = 0.85
    
    # Binary threshold value for isolating white text
    # Pixels above this become 255 (white), below become 0 (black)
    BINARY_THRESHOLD = 200
    
    # Enable debug mode - saves images and prints extra info
    # Set to False for production to avoid I/O overhead
    DEBUG_MODE = False
    
    # Wait time after F1 press for UI to render (seconds)
    UI_RENDER_DELAY = 0.3
    
    # Central ROI for text capture (relative to game window dimensions)
    # The "I need a Horse" text appears above the character's head at SCREEN CENTER
    # NOT in the top UI bar - so we capture the central 60% box
    # This avoids scanning UI bars (bottom) and minimap (top right)
    ROI_X_START = 0.20  # Start at 20% from left
    ROI_X_END = 0.80    # End at 80% from left (60% width)
    ROI_Y_START = 0.20  # Start at 20% from top
    ROI_Y_END = 0.80    # End at 80% from top (60% height)
    
    def __init__(self, 
                 template_path: str = "assets/text_need_horse.png",
                 hwnd: Optional[int] = None):
        """
        Initialize the mount checker.
        
        Args:
            template_path: Path to the "I need a Horse" text template image
            hwnd: Optional window handle to capture from specific window
        """
        self.template_path = template_path
        self.hwnd = hwnd
        self.template: Optional[np.ndarray] = None
        self.template_gray: Optional[np.ndarray] = None  # Grayscale version
        self.template_thresh: Optional[np.ndarray] = None  # Binary thresholded version
        self.template_loaded = False
        
        # NOTE: MSS instance is NOT stored as class attribute for thread-safety
        # Each capture creates a fresh mss instance using context manager
        # This prevents '_thread._local object has no attribute srcdc' errors
        
        # Load the template image
        self._load_template()
        
    def _load_template(self) -> bool:
        """
        Load the template image for matching.
        
        Returns:
            True if template loaded successfully, False otherwise
        """
        try:
            if not os.path.exists(self.template_path):
                logger.warning(f"Template not found: {self.template_path}")
                logger.warning("Mount detection will not work without the template image!")
                self.template_loaded = False
                return False
                
            self.template = cv2.imread(self.template_path)
            
            if self.template is None:
                logger.error(f"Failed to read template image: {self.template_path}")
                self.template_loaded = False
                return False
                
            self.template_loaded = True
            h, w = self.template.shape[:2]
            
            # Convert template to grayscale
            self.template_gray = cv2.cvtColor(self.template, cv2.COLOR_BGR2GRAY)
            
            # Apply binary threshold to isolate white text
            # Pixels > 200 become 255 (white), else 0 (black)
            _, self.template_thresh = cv2.threshold(
                self.template_gray, 
                self.BINARY_THRESHOLD, 
                255, 
                cv2.THRESH_BINARY
            )
            
            logger.info(f"Mount checker template loaded: {self.template_path} ({w}x{h})")
            
            return True
            
        except cv2.error as e:
            logger.error(f"OpenCV error loading template: {e}")
            self.template_loaded = False
            return False
        except Exception as e:
            logger.error(f"Unexpected error loading template: {e}")
            self.template_loaded = False
            return False
    
    def _capture_screen_region(self, game_region: Optional[dict] = None) -> Optional[np.ndarray]:
        """
        Capture the region where the warning text appears.
        
        THREAD-SAFE: Uses context manager to create fresh mss instance per capture.
        
        Args:
            game_region: Optional dict with window coordinates from ProcessManager:
                         {'left': x, 'top': y, 'width': w, 'height': h}
                         If provided, captures a sub-region of this window.
                         If None, falls back to full-screen percentage-based capture.
        
        Returns:
            BGR image of the captured region, or None on failure
        """
        if not HAS_MSS:
            logger.error("MSS not available for screen capture")
            print("[DEBUG] MSS not available. Install with: pip install mss")
            return None

        assert mss is not None
            
        try:
            # THREAD-SAFE: Create fresh mss instance for THIS capture
            with mss.mss() as sct:
                
                if game_region is not None:
                    # Use provided window region - calculate ROI within the window
                    window_left = game_region.get('left', 0)
                    window_top = game_region.get('top', 0)
                    window_width = game_region.get('width', 1920)
                    window_height = game_region.get('height', 1080)
                    
                    # Calculate ROI within the game window (relative to window)
                    x_start = window_left + int(window_width * self.ROI_X_START)
                    x_end = window_left + int(window_width * self.ROI_X_END)
                    y_start = window_top + int(window_height * self.ROI_Y_START)
                    y_end = window_top + int(window_height * self.ROI_Y_END)
                    
                    logger.debug(f"Capturing from game window: ({window_left}, {window_top}) {window_width}x{window_height}")
                    
                else:
                    # Fallback: Use full screen with percentage-based ROI
                    logger.warning("No game_region provided, using full screen capture")
                    monitor = sct.monitors[1]  # Primary monitor
                    screen_width = monitor["width"]
                    screen_height = monitor["height"]
                    
                    x_start = int(screen_width * self.ROI_X_START)
                    x_end = int(screen_width * self.ROI_X_END)
                    y_start = int(screen_height * self.ROI_Y_START)
                    y_end = int(screen_height * self.ROI_Y_END)
                
                # Define capture region for MSS
                region = {
                    "left": x_start,
                    "top": y_start,
                    "width": x_end - x_start,
                    "height": y_end - y_start
                }
                
                logger.debug(f"Capture region: left={region['left']}, top={region['top']}, {region['width']}x{region['height']}")
                
                # Capture
                screenshot = sct.grab(region)
                
                # Convert to numpy array (BGRA -> BGR)
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
                return frame
            
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            return None
    
    def _press_f1(self) -> bool:
        """
        Press the F1 key to trigger horse skill.
        
        Returns:
            True if key was pressed, False if pydirectinput unavailable
        """
        if not HAS_PYDIRECTINPUT:
            logger.warning("pydirectinput not available. Install with: pip install pydirectinput")
            return False

        assert pydirectinput is not None
            
        try:
            pydirectinput.press('f1')
            return True
        except Exception as e:
            logger.error(f"Failed to press F1: {e}")
            return False
    
    def _match_template(self, frame: np.ndarray) -> Tuple[bool, float, Optional[Tuple[int, int]], Optional[Tuple[int, int]], Optional[np.ndarray]]:
        """
        Perform template matching using BINARY THRESHOLDED images.
        
        Algorithm:
        1. Convert screenshot to grayscale
        2. Apply binary threshold (white text becomes pure white, background becomes black)
        3. Match thresholded template against thresholded screenshot
        4. This eliminates background noise completely
        
        Args:
            frame: BGR image to search in
            
        Returns:
            Tuple of (match_found, confidence, location, top_left, thresh_frame)
            - match_found: True if match above threshold
            - confidence: Match confidence (0.0 - 1.0)
            - location: (x, y) of match center, or None if not found
            - top_left: (x, y) of match top-left corner for drawing
            - thresh_frame: The thresholded screenshot for debug saving
        """
        if self.template is None or self.template_thresh is None:
            return False, 0.0, None, None, None
            
        try:
            # Step 1: Convert frame to grayscale
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Step 2: Apply binary threshold to screenshot
            # White text becomes pure white (255), background becomes black (0)
            _, frame_thresh = cv2.threshold(
                frame_gray, 
                self.BINARY_THRESHOLD, 
                255, 
                cv2.THRESH_BINARY
            )
            
            # Step 3: Match thresholded template against thresholded screenshot
            # Now we're comparing "white letters on black" vs "white letters on black"
            result = cv2.matchTemplate(frame_thresh, self.template_thresh, cv2.TM_CCOEFF_NORMED)
            
            # Get best match location and score
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # For TM_CCOEFF_NORMED, max_val is the best match confidence
            confidence = max_val
            top_left = (int(max_loc[0]), int(max_loc[1]))
            
            logger.debug(f"Mount Check Confidence: {confidence:.4f} (threshold: {self.MATCH_THRESHOLD})")
            
            if confidence >= self.MATCH_THRESHOLD:
                # Calculate center of matched region
                h, w = self.template_thresh.shape[:2]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                return True, confidence, (center_x, center_y), top_left, frame_thresh
            else:
                return False, confidence, None, top_left, frame_thresh
                
        except cv2.error as e:
            logger.error(f"Template matching error: {e}")
            return False, 0.0, None, None, None
        except Exception as e:
            logger.error(f"Unexpected error in template matching: {e}")
            print(f"[DEBUG] Unexpected error in template matching: {e}")
            return False, 0.0, None, None, None
    
    def check_is_unmounted(self, press_key: bool = True, game_region: Optional[dict] = None) -> bool:
        """
        Check if character is currently UNMOUNTED (not on horse).
        
        Algorithm:
        1. Press F1 (horse skill key)
        2. Wait for UI to render
        3. Capture game window area (or fallback to screen)
        4. Template match for "I need a Horse" warning
        5. Return True if warning found (UNMOUNTED), False otherwise (MOUNTED)
        
        Args:
            press_key: If True, press F1 before checking. Set False to just capture and match.
            game_region: Optional dict with window coordinates from ProcessManager:
                         {'left': x, 'top': y, 'width': w, 'height': h}
                         HIGHLY RECOMMENDED for accurate targeting.
        
        Returns:
            True if UNMOUNTED (warning text visible)
            False if MOUNTED (no warning text)
        """
        # Validate template is loaded
        if not self.template_loaded:
            logger.warning("Template not loaded. Attempting to reload...")
            if not self._load_template():
                logger.error("Cannot check mount status without template image!")
                return False  # Assume mounted to avoid breaking bot logic
        
        # Step 1: Press F1
        if press_key:
            if not self._press_f1():
                logger.warning("Could not press F1 key")
                return False  # Assume mounted if we can't test
        
        # Step 2: Wait for UI to render
        time.sleep(self.UI_RENDER_DELAY)
        
        # Step 3: Capture screen region (with explicit game window targeting)
        frame = self._capture_screen_region(game_region=game_region)
        if frame is None:
            logger.error("Failed to capture screen for mount check")
            return False  # Assume mounted on capture failure
        
        # Step 4: Template match
        match_found, confidence, location, top_left, thresh_frame = self._match_template(frame)
        
        # Step 5: Return result
        if match_found:
            logger.info(f"UNMOUNTED - 'I need a Horse' text found (confidence: {confidence:.2f})")
            return True
        else:
            logger.debug(f"MOUNTED - No warning text (best confidence: {confidence:.2f})")
            return False
    
    def is_mounted(self) -> bool:
        """
        Convenience method - returns True if MOUNTED, False if UNMOUNTED.
        Inverse of check_is_unmounted().
        """
        return not self.check_is_unmounted()
    
    def get_mount_status_string(self) -> str:
        """
        Get a human-readable mount status string.
        
        Returns:
            "MOUNTED" or "UNMOUNTED"
        """
        if self.check_is_unmounted():
            return "UNMOUNTED"
        else:
            return "MOUNTED"
    
    def release(self):
        """
        Release resources (no-op for thread-safe implementation).
        
        NOTE: MSS instances are now created per-capture using context managers,
        so there's nothing to release at class level.
        """
        pass  # No persistent resources to release
    
    def __del__(self):
        """Destructor (no-op for thread-safe implementation)."""
        pass  # No cleanup needed
