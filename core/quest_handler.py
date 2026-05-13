"""
Quest Handler - Auto Mission Book System
=========================================
Handles automatic quest renewal when the "Quest Completed" notification appears.

Features:
- Detects bottom-center quest completion notification
- Scans inventory (always open) for Mission Book item
- Opens quest dialog and selects target map
- Confirms dialog with existing dialog_confirm.png asset

Integration:
- Called every 3 seconds from bot_engine main loop
- Blocks movement/attack during quest cycle for stability
"""

import time
import cv2
import numpy as np
import pydirectinput
from typing import Optional, Tuple, Dict, Callable
from core.window_capture import WindowCapture


class QuestHandler:
    """
    Manages automatic mission book renewal.
    
    Workflow:
    1. check_notification() - Detects "Quest Completed" toast at screen bottom
    2. perform_cycle() - Right-clicks book, selects map, confirms dialog
    """
    
    def __init__(self, target_hwnd: Optional[int] = None):
        """
        Initialize QuestHandler with template images.
        
        Args:
            target_hwnd: Optional window handle for WindowCapture
        """
        # Templates for detection
        self.template_notification = self._load_template("assets/quest_notification.png")
        self.template_mission_book = self._load_template("assets/item_mission_book.png")
        self.template_map_text = self._load_template("assets/quest_map_text.png")
        self.template_confirm = self._load_template("assets/dialog_confirm.png")
        
        # State
        self.target_hwnd = target_hwnd
        self.capturer: Optional[WindowCapture] = None  # Set by BotEngine
        self.driver = None  # Set by BotEngine for mouse actions
        
        # Fail-safe Timer
        self.last_run_time = 0
        self.fallback_interval = 1200  # 20 minutes (1200 seconds)
        
        # Detection thresholds
        self.notification_threshold = 0.8
        self.book_threshold = 0.75
        self.map_threshold = 0.75
        self.confirm_threshold = 0.8
        
        # Cached frame for multi-step operations
        self._last_frame: Optional[np.ndarray] = None
        self._last_game_region: Optional[Dict[str, int]] = None
    
    def _load_template(self, path: str) -> Optional[np.ndarray]:
        """Load a template image from file."""
        try:
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                print(f"[QUEST] Warning: Could not load template: {path}")
            return img
        except Exception as e:
            print(f"[QUEST] Error loading template {path}: {e}")
            return None
            
    def should_run(self, frame: np.ndarray, game_region: Dict[str, int], log_callback: Optional[Callable] = None) -> bool:
        """
        Check if quest cycle should be triggered.
        
        Triggers if:
        1. Visual notification is detected (Primary)
        2. Timer has expired (Fail-safe Fallback)
        
        Args:
            frame: Current game screenshot
            game_region: Game window dimensions
            log_callback: Optional logging function
            
        Returns:
            True if cycle should run
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
                
        # 1. Check Fail-safe Timer
        if time.time() - self.last_run_time > self.fallback_interval:
            log(f"[QUEST] ⏰ Timer fallback triggered (> {self.fallback_interval}s)")
            return True
        
        # 2. Check Visual Notification
        if self.check_notification(frame, game_region):
            log("[QUEST] 👁️ Visual notification detected!")
            return True
            
        return False
    
    def check_notification(self, frame: np.ndarray, game_region: Dict[str, int]) -> bool:
        """
        Check if the quest completion notification is visible.
        
        The notification appears at the BOTTOM-CENTER of the game window.
        
        Args:
            frame: Current game screenshot (BGR format)
            game_region: Game window dimensions {left, top, width, height}
            
        Returns:
            True if notification detected with high confidence
        """
        _ = game_region

        if self.template_notification is None:
            return False
        
        if frame is None:
            return False
        
        h, w = frame.shape[:2]
        
        # Define ROI: Bottom-center area (where quest notifications appear)
        # Approximate: center 50% width, bottom 20% height
        roi_left = int(w * 0.25)
        roi_right = int(w * 0.75)
        roi_top = int(h * 0.75)
        roi_bottom = h
        
        # Extract ROI
        roi = frame[roi_top:roi_bottom, roi_left:roi_right]
        
        if roi.size == 0:
            return False
        
        # Template matching
        try:
            result = cv2.matchTemplate(roi, self.template_notification, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            
            if max_val >= self.notification_threshold:
                return True
                
        except cv2.error:
            # Template larger than ROI or other CV error
            pass
        
        return False
    
    def _find_template_in_region(
        self, 
        frame: np.ndarray, 
        template: Optional[np.ndarray], 
        roi: Tuple[int, int, int, int], 
        threshold: float
    ) -> Optional[Tuple[int, int]]:
        """
        Find template within a specific region of the frame.
        
        Args:
            frame: Full game screenshot
            template: Template to search for
            roi: (left, top, right, bottom) in frame coordinates
            threshold: Minimum confidence threshold
            
        Returns:
            (center_x, center_y) in FRAME coordinates, or None if not found
        """
        if template is None or frame is None:
            return None
        
        left, top, right, bottom = roi
        region = frame[top:bottom, left:right]
        
        if region.size == 0:
            return None
        
        # Check if template fits in region
        if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
            return None
        
        try:
            result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= threshold:
                # Convert to frame coordinates
                match_x = left + max_loc[0] + template.shape[1] // 2
                match_y = top + max_loc[1] + template.shape[0] // 2
                return (match_x, match_y)
                
        except cv2.error:
            pass
        
        return None
    
    def perform_cycle(  # NOSONAR
        self, 
        frame: np.ndarray, 
        game_region: Dict[str, int],
        log_callback: Optional[Callable] = None,
        capture_callback: Optional[Callable] = None
    ) -> bool:
        """
        Execute the complete quest renewal cycle.
        
        Sequence:
        1. Find Mission Book in inventory (right side of screen)
        2. Right-click the book to open dialog
        3. Find and click the target map text
        4. Find and click the confirmation button
        
        Args:
            frame: Current game screenshot
            game_region: Game window dimensions
            log_callback: Optional function for logging
            capture_callback: Function to capture fresh screenshots
            
        Returns:
            True if cycle completed successfully
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        if frame is None:
            log("[QUEST] Error: No frame provided")
            return False
            
        # Check if 1 second has passed since last run to prevent double execution
        # (Though should_run handles the timer, this is a safety check)
        
        h, w = frame.shape[:2]
        
        # =========================================================================
        # STEP 1: Find Mission Book in Inventory (RIGHT SIDE of screen)
        # Inventory is ALWAYS OPEN - just scan the right portion
        # =========================================================================
        log("[QUEST] Step 1: Looking for Mission Book in inventory...")
        
        # ROI: Right 35% of screen (inventory area)
        inv_roi = (int(w * 0.65), 0, w, h)
        book_pos = self._find_template_in_region(
            frame, 
            self.template_mission_book, 
            inv_roi, 
            self.book_threshold
        )
        
        if book_pos is None:
            log("[QUEST] Error: Mission Book not found in inventory!")
            return False
        
        local_x, local_y = book_pos
        log(f"[QUEST] Found Mission Book at local ({local_x}, {local_y})")
        
        # Convert to global screen coordinates
        if self.capturer and self.capturer.target_hwnd:
            global_x, global_y = self.capturer.get_screen_position(local_x, local_y)
        else:
            global_x = game_region.get('left', 0) + local_x
            global_y = game_region.get('top', 0) + local_y
        
        # =========================================================================
        # STEP 2: Right-click the Mission Book
        # =========================================================================
        log(f"[QUEST] Step 2: Right-clicking Mission Book at ({global_x}, {global_y})...")
        
        # Move first
        if self.driver:
            self.driver.move_abs(global_x, global_y)
        else:
            pydirectinput.moveTo(global_x, global_y)
            
        time.sleep(0.15)
        
        # Click
        if self.driver:
            self.driver.right_click(duration_ms=25)
        else:
            pydirectinput.rightClick()
        
        # Wait for dialog to open
        time.sleep(0.5)
        
        # =========================================================================
        # STEP 3: Capture fresh frame and find Map Text in dialog
        # =========================================================================
        log("[QUEST] Step 3: Looking for target map in dialog...")
        
        if capture_callback:
            fresh_frame = capture_callback()
        else:
            fresh_frame = frame  # Fallback to original (may be stale)
        
        if fresh_frame is None:
            log("[QUEST] Error: Could not capture fresh frame for map selection")
            return False
        
        # ROI: Center area of screen (dialog usually appears here)
        h2, w2 = fresh_frame.shape[:2]
        dialog_roi = (int(w2 * 0.2), int(h2 * 0.2), int(w2 * 0.8), int(h2 * 0.8))
        
        map_pos = self._find_template_in_region(
            fresh_frame, 
            self.template_map_text, 
            dialog_roi, 
            self.map_threshold
        )
        
        if map_pos is None:
            log("[QUEST] Error: Target map text not found in dialog!")
            return False
        
        local_x, local_y = map_pos
        log(f"[QUEST] Found target map at local ({local_x}, {local_y})")
        
        # Convert to global
        if self.capturer and self.capturer.target_hwnd:
            global_x, global_y = self.capturer.get_screen_position(local_x, local_y)
        else:
            global_x = game_region.get('left', 0) + local_x
            global_y = game_region.get('top', 0) + local_y
        
        # =========================================================================
        # STEP 4: Left-click the Map Text
        # =========================================================================
        log(f"[QUEST] Step 4: Clicking target map at ({global_x}, {global_y})...")
        
        if self.driver:
            self.driver.move_abs(global_x, global_y)
            time.sleep(0.15)
            self.driver.click(duration_ms=25)
        else:
            pydirectinput.moveTo(global_x, global_y)
            time.sleep(0.15)
            pydirectinput.click()
        
        # Wait for confirmation dialog
        time.sleep(0.3)
        
        # =========================================================================
        # STEP 5: Capture fresh frame and find Confirm button
        # =========================================================================
        log("[QUEST] Step 5: Looking for confirmation button...")
        
        if capture_callback:
            confirm_frame = capture_callback()
        else:
            confirm_frame = fresh_frame
        
        if confirm_frame is None:
            log("[QUEST] Error: Could not capture frame for confirmation")
            return False
        
        # ROI: Center area for confirmation dialog
        h3, w3 = confirm_frame.shape[:2]
        confirm_roi = (int(w3 * 0.2), int(h3 * 0.2), int(w3 * 0.8), int(h3 * 0.8))
        
        confirm_pos = self._find_template_in_region(
            confirm_frame, 
            self.template_confirm, 
            confirm_roi, 
            self.confirm_threshold
        )
        
        if confirm_pos is None:
            log("[QUEST] Error: Confirmation button not found!")
            return False
        
        local_x, local_y = confirm_pos
        log(f"[QUEST] Found confirm button at local ({local_x}, {local_y})")
        
        # Convert to global
        if self.capturer and self.capturer.target_hwnd:
            global_x, global_y = self.capturer.get_screen_position(local_x, local_y)
        else:
            global_x = game_region.get('left', 0) + local_x
            global_y = game_region.get('top', 0) + local_y
        
        # =========================================================================
        # STEP 6: Left-click the Confirm button
        # =========================================================================
        log(f"[QUEST] Step 6: Clicking confirm at ({global_x}, {global_y})...")
        
        if self.driver:
            self.driver.move_abs(global_x, global_y)
            time.sleep(0.15)
            self.driver.click(duration_ms=25)
        else:
            pydirectinput.moveTo(global_x, global_y)
            time.sleep(0.15)
            pydirectinput.click()
        
        # =========================================================================
        # CRITICAL: Reset Timer
        # =========================================================================
        self.last_run_time = time.time()
        
        log(f"[QUEST] ✓ Quest renewed successfully! Next fallback: {self.fallback_interval}s")
        return True
