"""
Smart Inventory Manager (Auto-Equipper)
Automatic equipment slot detection and item equipping system.
Uses template matching to detect empty equipment slots and equip items from inventory.

REACTIVE DESIGN:
- Check equipment slots every 5 seconds (configurable)
- Only equip items when their corresponding slot is EMPTY
- Handles confirmation dialogs after right-clicking items

ASSET REQUIREMENTS:
- assets/slot_glove_empty.png: Empty glove equipment slot appearance
- assets/item_glove_icon.png: Thief Glove item icon in inventory
- assets/dialog_confirm.png: Confirmation button that may appear after clicking
"""

import cv2
import numpy as np
import time
import os
import pydirectinput
from typing import Tuple, Optional, Dict, Any


# ===== DEBUG MODE =====
# Set to True to enable visual debugging (saves detection images to temp_debug/)
DEBUG_MODE = False


class InventoryManager:
    """
    Manages automatic equipment detection and item equipping.
    Detects empty equipment slots and equips corresponding items from inventory.
    
    REACTIVE APPROACH:
    - check_glove_slot(frame) -> Returns True if slot is EMPTY (needs item)
    - find_glove_in_inventory(frame) -> Returns (x, y) coordinates of item or None
    - equip_glove() -> Handles the full equip sequence with confirmation handling
    - check_and_equip(frame) -> Main entry point, runs the full logic flow
    """
    
    def __init__(self, assets_dir="assets"):
        """
        Initialize the inventory manager.
        
        Args:
            assets_dir: Directory containing template images
        """
        self.assets_dir = assets_dir
        
        # Template matching threshold
        # LOWERED for better detection - can be increased if false positives occur
        self.empty_slot_threshold = 0.75  # Lowered from 0.9 for better detection
        self.item_icon_threshold = 0.75   # Lowered from 0.85
        self.confirm_button_threshold = 0.70  # Lowered from 0.8
        
        # ============================================================
        # ROI DEFINITIONS - SET TO NONE TO SEARCH ENTIRE SCREEN
        # ============================================================
        # Setting these to None will search the ENTIRE frame
        # You can enable ROIs later for performance optimization
        
        # Equipment Window ROI - DISABLED (searches entire frame)
        self.equipment_roi = None  # Set to {'x': 0, 'y': 0, 'w': 300, 'h': 400} to enable
        
        # Inventory Window ROI - DISABLED (searches entire frame)
        self.inventory_roi = None  # Set to {'x': 0, 'y': 400, 'w': 300, 'h': 300} to enable
        
        # Dialog/Screen Center ROI (where confirmation dialogs appear)
        self.dialog_roi = {
            'x_ratio': 0.2,  # 20% from left edge (wider search)
            'y_ratio': 0.2,  # 20% from top edge (wider search)
            'w_ratio': 0.6,  # 60% width (center portion)
            'h_ratio': 0.6   # 60% height (center portion)
        }
        
        # Load templates
        self.templates = {}
        self._load_templates()
        
        # Timing - cooldown to prevent spam
        self.last_check_time = 0
        self.check_interval = 5.0  # Check every 5 seconds (CPU efficient)
        
        # Last equip attempt time (to prevent rapid fire)
        self.last_equip_attempt = 0
        self.equip_cooldown = 10.0  # Wait 10 seconds between equip attempts
        
        # Statistics
        self.total_equips = 0
        self.total_checks = 0
        self.last_status: Dict[str, Optional[bool]] = {'glove_empty': None, 'glove_found': None}
        self.last_confidence = {'slot': 0.0, 'item': 0.0, 'confirm': 0.0}
        
        # Coordinate storage for the found item
        self._last_item_coords = None
        
        # Driver reference (set by bot_engine)
        self.driver: Optional[Any] = None
        self.capturer: Optional[Any] = None
        
        # Debug directory
        if DEBUG_MODE:
            os.makedirs("temp_debug", exist_ok=True)
            print(f"[InventoryManager] DEBUG MODE ENABLED - Images saved to temp_debug/")

    
    def _load_templates(self):
        """Load template images from assets directory."""
        template_files = {
            'slot_glove_empty': 'slot_glove_empty.png',
            'item_glove_icon': 'item_glove_icon.png',
            'dialog_confirm': 'dialog_confirm.png'
        }
        
        for template_name, filename in template_files.items():
            template_path = os.path.join(self.assets_dir, filename)
            
            if os.path.exists(template_path):
                template = cv2.imread(template_path, cv2.IMREAD_COLOR)
                if template is not None:
                    self.templates[template_name] = template
                    print(f"[InventoryManager] Loaded template: {template_name} ({template.shape[1]}x{template.shape[0]})")
                else:
                    print(f"[InventoryManager] [WARN] Failed to load: {template_path}")
            else:
                print(f"[InventoryManager] [WARN] Template not found: {template_path}")
        
        if len(self.templates) == 0:
            print(f"[InventoryManager] [ERROR] No templates loaded! Equipment detection will not work.")
            print(f"[InventoryManager] [INFO] Please place template images in: {self.assets_dir}/")
            print(f"[InventoryManager] [INFO] Required files:")
            print(f"  - slot_glove_empty.png: Empty glove equipment slot")
            print(f"  - item_glove_icon.png: Thief Glove item icon")
            print(f"  - dialog_confirm.png: Confirmation button")
    
    def _get_roi_frame(self, frame: np.ndarray, roi: Dict) -> Optional[np.ndarray]:
        """Extract Region of Interest from the frame."""
        if frame is None:
            return None
        
        try:
            roi_frame = frame[
                roi['y']:roi['y'] + roi['h'],
                roi['x']:roi['x'] + roi['w']
            ]
            return roi_frame if roi_frame.size > 0 else None
        except Exception as e:
            print(f"[InventoryManager] [ERROR] ROI extraction failed: {e}")
            return None
    
    def _get_dialog_roi_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Extract center portion of frame where dialogs typically appear."""
        if frame is None:
            return None
        
        try:
            h, w = frame.shape[:2]
            x_start = int(w * self.dialog_roi['x_ratio'])
            y_start = int(h * self.dialog_roi['y_ratio'])
            roi_w = int(w * self.dialog_roi['w_ratio'])
            roi_h = int(h * self.dialog_roi['h_ratio'])
            
            roi_frame = frame[y_start:y_start + roi_h, x_start:x_start + roi_w]
            return roi_frame if roi_frame.size > 0 else None
        except Exception as e:
            print(f"[InventoryManager] [ERROR] Dialog ROI extraction failed: {e}")
            return None
    
    def _find_template(self, frame: np.ndarray, template: np.ndarray, threshold: float) -> Tuple[bool, Optional[Tuple[int, int]], float]:
        """
        Find template in frame using template matching.
        
        Args:
            frame: Frame to search in
            template: Template to search for
            threshold: Minimum confidence threshold
        
        Returns:
            Tuple of (found: bool, position: (x, y) or None, confidence: float)
        """
        try:
            # Ensure dimensions are valid
            if frame.shape[0] < template.shape[0] or frame.shape[1] < template.shape[1]:
                return (False, None, 0.0)
            
            # Template matching
            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # Check if match exceeds threshold
            if max_val >= threshold:
                # Calculate center of matched region
                h, w = template.shape[:2]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                return (True, (center_x, center_y), max_val)
            
            return (False, None, max_val)
            
        except Exception as e:
            print(f"[InventoryManager] [ERROR] Template matching failed: {e}")
            return (False, None, 0.0)
    
    # ==========================================================================
    # DETECTION METHODS
    # ==========================================================================
    
    def check_glove_slot(self, frame: np.ndarray) -> bool:
        """
        Check if the glove equipment slot is EMPTY.
        
        Args:
            frame: Current game frame (BGR format)
        
        Returns:
            True if glove slot is EMPTY (needs equipping), False if occupied
        """
        if frame is None:
            print("[InventoryManager] [ERROR] Frame is None!")
            return False
            
        if 'slot_glove_empty' not in self.templates:
            print("[InventoryManager] [ERROR] slot_glove_empty template not loaded!")
            return False
        
        # Determine search region
        if self.equipment_roi is not None:
            roi_frame = self._get_roi_frame(frame, self.equipment_roi)
            if roi_frame is None:
                print("[InventoryManager] [WARN] ROI extraction failed, searching entire frame")
                roi_frame = frame
        else:
            # Search entire frame (ROI disabled)
            roi_frame = frame
        
        # Perform template matching
        found, pos, confidence = self._find_template(
            roi_frame, 
            self.templates['slot_glove_empty'], 
            self.empty_slot_threshold
        )
        
        # Store confidence for debugging
        self.last_confidence['slot'] = confidence
        
        # ALWAYS log in debug mode
        if DEBUG_MODE:
            print(f"[InventoryManager] [DEBUG] Slot check - Found: {found}, Confidence: {confidence:.3f}, Threshold: {self.empty_slot_threshold}")
            
            # Save debug images
            try:
                timestamp = int(time.time())
                template = self.templates['slot_glove_empty']
                
                # Draw detection result on frame copy
                debug_frame = roi_frame.copy()
                if pos:
                    th, tw = template.shape[:2]
                    top_left = (pos[0] - tw//2, pos[1] - th//2)
                    bottom_right = (pos[0] + tw//2, pos[1] + th//2)
                    color = (0, 255, 0) if found else (0, 0, 255)  # Green if found, Red if not
                    cv2.rectangle(debug_frame, top_left, bottom_right, color, 2)
                    cv2.putText(debug_frame, f"Conf: {confidence:.3f}", (top_left[0], top_left[1] - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                
                # Save debug images
                cv2.imwrite(f"temp_debug/slot_check_{timestamp}.jpg", debug_frame)
                cv2.imwrite(f"temp_debug/slot_template.png", template)
                print(f"[InventoryManager] [DEBUG] Saved: temp_debug/slot_check_{timestamp}.jpg")
            except Exception as e:
                print(f"[InventoryManager] [DEBUG] Image save error: {e}")
        
        # Log status change
        if found != self.last_status['glove_empty']:
            status_str = "EMPTY ✗" if found else "EQUIPPED ✓"
            print(f"[InventoryManager] Glove slot: {status_str} (conf: {confidence:.3f}, threshold: {self.empty_slot_threshold})")
            self.last_status['glove_empty'] = found
        
        return found
    
    def find_glove_in_inventory(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """
        Find the Thief Glove item in the inventory.
        
        Args:
            frame: Current game frame (BGR format)
        
        Returns:
            (x, y) local coordinates of item center if found, None otherwise
        """
        if frame is None:
            print("[InventoryManager] [ERROR] Frame is None!")
            return None
            
        if 'item_glove_icon' not in self.templates:
            print("[InventoryManager] [ERROR] item_glove_icon template not loaded!")
            return None
        
        # Determine search region
        roi_offset_x = 0
        roi_offset_y = 0
        
        if self.inventory_roi is not None:
            roi_frame = self._get_roi_frame(frame, self.inventory_roi)
            roi_offset_x = self.inventory_roi['x']
            roi_offset_y = self.inventory_roi['y']
            
            if roi_frame is None:
                print("[InventoryManager] [WARN] ROI extraction failed, searching entire frame")
                roi_frame = frame
                roi_offset_x = 0
                roi_offset_y = 0
        else:
            # Search entire frame (ROI disabled)
            roi_frame = frame
        
        found, pos, confidence = self._find_template(
            roi_frame, 
            self.templates['item_glove_icon'], 
            self.item_icon_threshold
        )
        
        # Store confidence for debugging
        self.last_confidence['item'] = confidence
        
        # Debug logging
        if DEBUG_MODE:
            print(f"[InventoryManager] [DEBUG] Item search - Found: {found}, Confidence: {confidence:.3f}, Threshold: {self.item_icon_threshold}")
        
        if found and pos:
            # Convert ROI-local coordinates to frame-local coordinates
            global_x = pos[0] + roi_offset_x
            global_y = pos[1] + roi_offset_y
            
            # Log status change
            if not self.last_status['glove_found']:
                print(f"[InventoryManager] Thief Glove FOUND at ({global_x}, {global_y}) (conf: {confidence:.3f})")
                self.last_status['glove_found'] = True
            
            self._last_item_coords = (global_x, global_y)
            return (global_x, global_y)
        else:
            if self.last_status['glove_found']:
                print(f"[InventoryManager] Thief Glove not found in inventory (conf: {confidence:.3f})")
                self.last_status['glove_found'] = False
            return None
    
    def find_confirm_button(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """
        Find the confirmation button in dialogs (usually appears at screen center).
        
        Args:
            frame: Current game frame (BGR format)
        
        Returns:
            (x, y) local coordinates of button center if found, None otherwise
        """
        if frame is None or 'dialog_confirm' not in self.templates:
            return None
        
        # Search in dialog ROI (center of screen)
        h, w = frame.shape[:2]
        x_start = int(w * self.dialog_roi['x_ratio'])
        y_start = int(h * self.dialog_roi['y_ratio'])
        
        roi_frame = self._get_dialog_roi_frame(frame)
        if roi_frame is None:
            roi_frame = frame
            x_start = 0
            y_start = 0
        
        found, pos, confidence = self._find_template(
            roi_frame, 
            self.templates['dialog_confirm'], 
            self.confirm_button_threshold
        )
        
        if found and pos:
            # Convert ROI-local to frame-local coordinates
            global_x = pos[0] + x_start
            global_y = pos[1] + y_start
            print(f"[InventoryManager] Confirm button FOUND at ({global_x}, {global_y}) (conf: {confidence:.2f})")
            return (global_x, global_y)
        
        return None
    
    # ==========================================================================
    # ACTION METHODS
    # ==========================================================================
    
    def _right_click_item(self, local_x: int, local_y: int, log_callback=None) -> bool:
        """
        Right-click on an item to use/equip it.
        
        Args:
            local_x, local_y: Local coordinates relative to game window
            log_callback: Optional callback for logging
        
        Returns:
            True if click was executed successfully
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        try:
            # Convert local to global screen coordinates
            if self.capturer and self.capturer.target_hwnd:
                screen_pos = self.capturer.get_screen_position(local_x, local_y)
                if isinstance(screen_pos, tuple) and len(screen_pos) == 2:
                    global_x, global_y = int(screen_pos[0]), int(screen_pos[1])
                else:
                    log("[InventoryManager] [WARN] Invalid screen position, using local coords")
                    global_x, global_y = local_x, local_y
            else:
                log("[InventoryManager] [WARN] No capturer, using local coords directly")
                global_x, global_y = local_x, local_y
            
            log(f"[InventoryManager] Right-clicking item at ({global_x}, {global_y})")
            
            # Move mouse to item position
            if self.driver:
                self.driver.move_abs(global_x, global_y)
                time.sleep(0.2)  # Wait for mouse to settle
                
                # Right-click to use/equip
                self.driver.right_click(duration_ms=50)
            else:
                # Fallback to pydirectinput
                pydirectinput.moveTo(global_x, global_y)
                time.sleep(0.2)
                pydirectinput.rightClick()
            
            return True
            
        except Exception as e:
            log(f"[InventoryManager] [ERROR] Right-click failed: {e}")
            return False
    
    def _click_confirm(self, local_x: int, local_y: int, log_callback=None) -> bool:
        """
        Click the confirmation button in a dialog.
        
        Args:
            local_x, local_y: Local coordinates relative to game window
            log_callback: Optional callback for logging
        
        Returns:
            True if click was executed successfully
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        try:
            # Convert local to global screen coordinates
            if self.capturer and self.capturer.target_hwnd:
                screen_pos = self.capturer.get_screen_position(local_x, local_y)
                if isinstance(screen_pos, tuple) and len(screen_pos) == 2:
                    global_x, global_y = int(screen_pos[0]), int(screen_pos[1])
                else:
                    log("[InventoryManager] [WARN] Invalid screen position, using local coords")
                    global_x, global_y = local_x, local_y
            else:
                global_x, global_y = local_x, local_y
            
            log(f"[InventoryManager] Clicking confirm button at ({global_x}, {global_y})")
            
            # Move and left-click
            if self.driver:
                self.driver.move_abs(global_x, global_y)
                time.sleep(0.1)
                self.driver.click(duration_ms=25)
            else:
                pydirectinput.moveTo(global_x, global_y)
                time.sleep(0.1)
                pydirectinput.click()
            
            return True
            
        except Exception as e:
            log(f"[InventoryManager] [ERROR] Confirm click failed: {e}")
            return False
    
    # ==========================================================================
    # MAIN ENTRY POINT
    # ==========================================================================
    
    def can_check(self) -> bool:
        """
        Check if enough time has passed since last check.
        Prevents excessive CPU usage by enforcing cooldown.
        
        Returns:
            True if check is allowed, False if on cooldown
        """
        return (time.time() - self.last_check_time) >= self.check_interval
    
    def can_equip(self) -> bool:
        """
        Check if enough time has passed since last equip attempt.
        Prevents rapid fire equip attempts.
        
        Returns:
            True if equip is allowed, False if on cooldown
        """
        return (time.time() - self.last_equip_attempt) >= self.equip_cooldown
    
    def check_and_equip(self, frame: np.ndarray, game_region: Optional[Dict[str, int]] = None,
                        log_callback=None, capture_callback=None) -> bool:
        """
        MAIN ENTRY POINT: Check equipment slot and equip item if needed.
        
        This method should be called periodically (e.g., every loop iteration).
        It handles its own cooldown internally.
        
        Algorithm:
        1. Check if cooldown allows checking
        2. Check if glove slot is EMPTY (using high confidence threshold)
        3. If empty, find Thief Glove in inventory
        4. If found, right-click to equip
        5. Wait for latency, then check for confirmation dialog
        6. If dialog found, click confirm button
        
        Args:
            frame: Current game frame (BGR format)
            game_region: Game window region info (for coordinate conversion)
            log_callback: Optional callback for logging
            capture_callback: Optional callback to get fresh screenshot (for dialog detection)
        
        Returns:
            True if any action was performed, False otherwise
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        # Check cooldown
        if not self.can_check():
            return False
        
        self.last_check_time = time.time()
        self.total_checks += 1
        
        log(f"[AUTO-EQUIP] === Check #{self.total_checks} başladı ===")
        
        # Step 1: Check if glove slot is EMPTY
        slot_empty = self.check_glove_slot(frame)
        
        if not slot_empty:
            # Slot is occupied (item is already equipped)
            return False
        
        log("[InventoryManager] Glove slot is EMPTY! Attempting to equip...")
        
        # Check equip cooldown
        if not self.can_equip():
            remaining = self.equip_cooldown - (time.time() - self.last_equip_attempt)
            log(f"[InventoryManager] Equip on cooldown ({remaining:.1f}s remaining)")
            return False
        
        # Step 2: Find Thief Glove in inventory
        item_pos = self.find_glove_in_inventory(frame)
        
        if item_pos is None:
            log("[InventoryManager] [WARN] Thief Glove not found in inventory!")
            log("[InventoryManager] [INFO] Make sure the item is visible on the current inventory page.")
            return False
        
        local_x, local_y = item_pos
        log(f"[InventoryManager] Found Thief Glove at ({local_x}, {local_y})")
        
        # Step 3: Right-click to equip
        self.last_equip_attempt = time.time()
        
        if not self._right_click_item(local_x, local_y, log_callback):
            log("[InventoryManager] [ERROR] Failed to right-click item")
            return False
        
        log("[InventoryManager] Right-clicked item. Waiting for server response...")
        
        # Step 4: Wait for latency (server response)
        time.sleep(0.5)
        
        # Step 5: Check for confirmation dialog
        # Get fresh screenshot if callback is available
        if capture_callback:
            fresh_frame = capture_callback()
        else:
            fresh_frame = frame  # Use same frame (less accurate)
        
        if fresh_frame is not None:
            confirm_pos = self.find_confirm_button(fresh_frame)
            
            if confirm_pos:
                log("[InventoryManager] Confirmation dialog detected!")
                
                # Step 6: Click confirm button
                conf_x, conf_y = confirm_pos
                if self._click_confirm(conf_x, conf_y, log_callback):
                    log("[InventoryManager] ✓ Confirmation clicked!")
                else:
                    log("[InventoryManager] [WARN] Failed to click confirm button")
            else:
                log("[InventoryManager] No confirmation dialog (item may have equipped directly)")
        
        # Update statistics
        self.total_equips += 1
        log(f"[InventoryManager] ✓ Equip sequence complete! (Total: {self.total_equips})")
        
        return True
    
    def set_rois(
        self,
        equipment_roi: Optional[Dict[str, int]] = None,
        inventory_roi: Optional[Dict[str, int]] = None,
    ):
        """
        Configure the Regions of Interest for equipment and inventory detection.
        
        Call this method to calibrate the ROIs based on your game's UI layout.
        Coordinates are relative to the game window (local coordinates).
        
        Args:
            equipment_roi: Dict with 'x', 'y', 'w', 'h' for equipment window area
            inventory_roi: Dict with 'x', 'y', 'w', 'h' for inventory window area
        """
        if equipment_roi:
            self.equipment_roi = equipment_roi
            print(f"[InventoryManager] Equipment ROI set: {equipment_roi}")
        
        if inventory_roi:
            self.inventory_roi = inventory_roi
            print(f"[InventoryManager] Inventory ROI set: {inventory_roi}")
    
    def get_stats(self) -> dict:
        """
        Get inventory manager statistics.
        
        Returns:
            Dictionary with stats
        """
        return {
            'total_equips': self.total_equips,
            'last_check_time': self.last_check_time,
            'last_equip_attempt': self.last_equip_attempt,
            'check_interval': self.check_interval,
            'equip_cooldown': self.equip_cooldown,
            'templates_loaded': len(self.templates),
            'glove_slot_empty': self.last_status.get('glove_empty'),
            'glove_in_inventory': self.last_status.get('glove_found')
        }


# ==========================================================================
# STANDALONE TEST
# ==========================================================================
if __name__ == "__main__":
    import sys
    
    # Test template loading
    manager = InventoryManager()
    
    print("\n" + "="*60)
    print(f"Inventory Manager Test (Auto-Equipper)")
    print("="*60)
    print(f"Templates loaded: {len(manager.templates)}")
    print(f"Check interval: {manager.check_interval}s")
    print(f"Equip cooldown: {manager.equip_cooldown}s")
    print(f"Empty slot threshold: {manager.empty_slot_threshold}")
    print(f"Item icon threshold: {manager.item_icon_threshold}")
    
    # Test with dummy frame
    dummy_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    
    print("\n--- Detection Tests (Dummy Frame) ---")
    slot_empty = manager.check_glove_slot(dummy_frame)
    print(f"Glove slot empty: {slot_empty}")
    
    item_pos = manager.find_glove_in_inventory(dummy_frame)
    print(f"Glove in inventory: {item_pos}")
    
    print("\n--- Stats ---")
    stats = manager.get_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    print("\n[INFO] To test with real game:")
    print("  1. Place template screenshots in assets/")
    print("  2. Calibrate ROIs using set_rois() method")
    print("  3. Integrate into bot_engine.py main loop")
    print("="*60)
