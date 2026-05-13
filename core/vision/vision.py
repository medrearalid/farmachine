import cv2
import numpy as np
import os
from typing import Tuple, Optional, Dict, Any, List
from core.utils.path_util import resource_path
import random

class Vision:
    # CAPTCHA dialog geometry calibrated from client.png using captcha.png anchor.
    # client.png absolute crop (x1,y1,x2,y2) = (332,204,666,544)
    # header (captcha.png) match in client.png = (409,208,w=180,h=28)
    # => dialog origin relative to header: x = header_center - 167, y = header_top - 4
    CAPTCHA_DIALOG_WIDTH = 334
    CAPTCHA_DIALOG_HEIGHT = 340
    CAPTCHA_DIALOG_TOP_OFFSET = -4

    # 2x3 tile layout inside calibrated dialog crop.
    CAPTCHA_TILE_START_X = 27
    CAPTCHA_TILE_START_Y = 41
    CAPTCHA_TILE_SIZE = 81
    CAPTCHA_TILE_GAP_X = 18
    CAPTCHA_TILE_GAP_Y = 17
    CAPTCHA_SAFE_ZONE_RATIO = 0.72

    @staticmethod
    def load_image(relative_path: str) -> Optional[np.ndarray]:
        """Load an image from assets using resource_path"""
        full_path = resource_path(relative_path)
        if not os.path.exists(full_path):
            return None
        return cv2.imread(full_path)

    @staticmethod
    def get_captcha_box_rect(image_index: int) -> Optional[Tuple[int, int, int, int]]:
        """Return (left, top, width, height) for captcha box index 1..6 in dialog-local coords."""
        if image_index < 1 or image_index > 6:
            return None

        row = (image_index - 1) // 3
        col = (image_index - 1) % 3

        left = Vision.CAPTCHA_TILE_START_X + col * (Vision.CAPTCHA_TILE_SIZE + Vision.CAPTCHA_TILE_GAP_X)
        top = Vision.CAPTCHA_TILE_START_Y + row * (Vision.CAPTCHA_TILE_SIZE + Vision.CAPTCHA_TILE_GAP_Y)
        return (left, top, Vision.CAPTCHA_TILE_SIZE, Vision.CAPTCHA_TILE_SIZE)

    @staticmethod
    def get_captcha_click_point(
        grid_origin: Tuple[int, int],
        image_index: int,
        frame_shape: Tuple[int, ...],
        safe_zone_ratio: Optional[float] = None,
        rng: Optional[Any] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Convert 1..6 captcha index to screenshot-local click coordinates.

        Args:
            grid_origin: (x, y) top-left of captcha dialog crop in screenshot-local coords
            image_index: 1..6 (Top-Left to Bottom-Right)
            frame_shape: screenshot shape for boundary clamp
            safe_zone_ratio: central area ratio used for randomized click point
            rng: optional random provider (must support uniform)
        """
        rect = Vision.get_captcha_box_rect(image_index)
        if rect is None:
            return None

        frame_h = int(frame_shape[0]) if frame_shape else 0
        frame_w = int(frame_shape[1]) if len(frame_shape) > 1 else 0
        if frame_w <= 0 or frame_h <= 0:
            return None

        origin_x = int(grid_origin[0])
        origin_y = int(grid_origin[1])
        box_left, box_top, box_w, box_h = rect

        ratio = safe_zone_ratio if safe_zone_ratio is not None else Vision.CAPTCHA_SAFE_ZONE_RATIO
        ratio = max(0.40, min(0.90, float(ratio)))

        safe_w = box_w * ratio
        safe_h = box_h * ratio
        safe_left = origin_x + box_left + (box_w - safe_w) * 0.5
        safe_top = origin_y + box_top + (box_h - safe_h) * 0.5

        random_source = rng if rng is not None else random
        click_x = int(safe_left + random_source.uniform(0.0, safe_w))
        click_y = int(safe_top + random_source.uniform(0.0, safe_h))

        click_x = max(0, min(frame_w - 1, click_x))
        click_y = max(0, min(frame_h - 1, click_y))
        return (click_x, click_y)

    @staticmethod
    def detect_template(screenshot: np.ndarray, template_path: str, threshold: float = 0.7) -> bool:
        """
        Detect if a template exists in the screenshot.
        """
        try:
            template = Vision.load_image(template_path)
            if template is None:
                return False
            
            # Convert screenshot to BGR if needed
            if len(screenshot.shape) == 3 and screenshot.shape[2] == 3: # RGB/BGR
                 # Assuming screenshot comes from pyautogui/PIL which is RGB, convert to BGR for OpenCV
                 # But if it comes from cv2.imread it is BGR. 
                 # Let's assume input is BGR (standard OpenCV) or handle conversion if we know source.
                 # For now, assuming standard BGR or compatible.
                 pass

            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            return max_val >= threshold
        except Exception as e:
            print(f"Template detection error: {e}")
            return False

    # @staticmethod
    # def detect_metin_stones(screenshot: np.ndarray, hsv_mask: Tuple[Tuple[int, int, int], Tuple[int, int, int]]) -> List[Dict[str, Any]]:
    #     """
    #     DEPRECATED: Replaced by YOLOv8 (core/vision_ai.py)
    #     Detect metin stones using HSV mask with advanced geometric filtering.
    #     """
    #     try:
    #         if hsv_mask is None:
    #             print("[Vision] HATA: HSV mask None!")
    #             return []
    #         
    #         img_height, img_width = screenshot.shape[:2]
    #         bottom_threshold = img_height * 0.93   # Bottom 7% is UI panel
    #         # Minimap area: right 18% and top 25%
    #         minimap_right = img_width * 0.82
    #         minimap_bottom = img_height * 0.25
    #         
    #         hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
    #         lower_bound, upper_bound = hsv_mask
    #         
    #         # Debug: Print HSV bounds
    #         # print(f"[Vision] HSV Lower: {lower_bound}, Upper: {upper_bound}")
    #         
    #         mask = cv2.inRange(hsv, np.array(lower_bound), np.array(upper_bound))
    #         
    #         # Morphology - daha yumusak (kucuk taslari korumak icin)
    #         # Sadece CLOSE islemi yap, OPEN yapma (kucuk noktalar silinmesin)
    #         kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    #         mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    #         
    #         contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    #         
    #         valid_stones = []
    #         for contour in contours:
    #             # ===== FILTER 1: Area Constraints (Size) =====
    #             area = cv2.contourArea(contour)
    #             # Min: 20px (daha dusuk - kucuk taslari da yakala)
    #             # Max: 5000px (binalar daha buyuk olur)
    #             if area < 20 or area > 5000:
    #                 continue
    #             
    #             # Get bounding box for position and shape checks
    #             x, y, w, h = cv2.boundingRect(contour)
    #             if h == 0:
    #                 continue
    #             
    #             # Calculate center coordinates using moments (more accurate centroid)
    #             M = cv2.moments(contour)
    #             if M["m00"] > 0:
    #                 cX = int(M["m10"] / M["m00"])
    #                 cY = int(M["m01"] / M["m00"])
    #             else:
    #                 # Fallback to bounding box center
    #                 cX = x + w // 2
    #                 cY = y + h // 2
    #             
    #             # ===== FILTER 2: Bottom Panel Filter =====
    #             # Ignore anything in the bottom 7% (UI panel)
    #             if cY > bottom_threshold:
    #                 continue
    #             
    #             # ===== FILTER 3: Minimap Filter (Top-Right corner) =====
    #             # Ignore right side AND top area (minimap region)
    #             if cX > minimap_right and cY < minimap_bottom:
    #                 continue
    #             
    #             # ===== FILTER 4: Aspect Ratio Filter (Shape) =====
    #             aspect_ratio = float(w) / h
    #             # Metin taslari kompakt (0.5-2.0), binalar uzun/dikdortgen
    #             if aspect_ratio < 0.5 or aspect_ratio > 2.0:
    #                 continue
    #             
    #             # ===== FILTER 5: Circularity Filter (Dairesellik) =====
    #             # Metin taslari yuvarlak/oval, binalar duzensiz
    #             perimeter = cv2.arcLength(contour, True)
    #             if perimeter > 0:
    #                 circularity = 4 * np.pi * area / (perimeter * perimeter)
    #                 # Metin taslari: circularity > 0.3 (daireye yakin)
    #                 # Binalar/yapilar: circularity < 0.3 (duzensiz/uzun)
    #                 if circularity < 0.25:
    #                     continue
    #             
    #             # ===== FILTER 6: Sol Ust Kose Filtresi (Bina Alani) =====
    #             # Sol %18 ve ust %35 bolgesi genelde bina/yapi iceriyor
    #             left_threshold = int(img_width * 0.18)  # Sol %18
    #             top_threshold = int(img_height * 0.35)   # Ust %35
    #             if cX < left_threshold and cY < top_threshold:
    #                 continue
    #             
    #             # ===== FILTER 7: Cok Buyuk Yukseklik Filtresi =====
    #             # Binalar dikey olarak cok uzun olur (h > 150px)
    #             if h > 120:
    #                 continue
    #             
    #             # All filters passed - valid stone candidate
    #             valid_stones.append({
    #                 'center': (cX, cY),
    #                 'area': area,
    #                 'contour': contour,
    #                 'bbox': (x, y, w, h),
    #                 'aspect_ratio': aspect_ratio
    #             })
    #         
    #         return valid_stones
    #     except Exception as e:
    #         print(f"Metin detection error: {e}")
    #         return []

    # @staticmethod
    # def get_best_metin(stones: List[Dict[str, Any]], screen_center: Tuple[int, int]) -> Optional[Dict[str, Any]]:
    #     """
    #     DEPRECATED: Replaced by YOLOv8 Logic
    #     """
    #     if not stones:
    #         return None
    #     
    #     def calculate_distance(stone):
    #         cx, cy = stone['center']
    #         return np.sqrt((cx - screen_center[0])**2 + (cy - screen_center[1])**2)
    #     
    #     # Simply return the closest stone
    #     closest_stone = min(stones, key=calculate_distance)
    #     return closest_stone

    @staticmethod
    def calculate_image_difference(img1: Optional[np.ndarray], img2: Optional[np.ndarray]) -> float:
        """
        Calculate difference score between two images.
        Converts to grayscale and uses absdiff.
        Returns mean difference (0 = identical, higher = more different).
        """
        if img1 is None or img2 is None:
            return float('inf')
        
        try:
            # Ensure same size
            if img1.shape != img2.shape:
                h = min(img1.shape[0], img2.shape[0])
                w = min(img1.shape[1], img2.shape[1])
                img1 = img1[:h, :w]
                img2 = img2[:h, :w]
            
            # Convert to grayscale
            if len(img1.shape) == 3:
                gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
            else:
                gray1 = img1
            
            if len(img2.shape) == 3:
                gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
            else:
                gray2 = img2
            
            # Calculate absolute difference
            diff = cv2.absdiff(gray1, gray2)
            mean_diff = float(np.mean(diff))
            
            return mean_diff
        except Exception as e:
            print(f"❌ Image difference calculation error: {e}")
            return float('inf')

    @staticmethod
    def is_hp_bar_visible(screenshot: np.ndarray, game_region: Optional[Dict[str, int]] = None, threshold: float = 0.7) -> tuple:
        """
        Check if the metin stone's HP bar is visible in the screenshot.
        
        OPTIMIZATION: Uses Region of Interest (ROI) scanning at the top-center of the screen.
        In Metin2, the Target HP Bar ALWAYS appears at the **Top-Center**, so we only scan
        that region instead of the entire screen - providing ~3x performance improvement.
        
        Args:
            screenshot: Full game screenshot (BGR format)
            game_region: Optional game region dict {'left': x, 'top': y, 'width': w, 'height': h}
                        If provided, uses ROI scanning; otherwise falls back to full-screen search
            threshold: Confidence threshold for template matching (default 0.7)
        
        Returns:
            Tuple[found: bool, cropped_hp_image: Optional[np.ndarray]]
            - found: True if HP bar detected
            - cropped_hp_image: The cropped region around the matched template, or None
        """
        try:
            # Use resource_path to locate the HP bar image
            img_path = resource_path(os.path.join("assets", "metin_hp.png"))
            
            if not os.path.exists(img_path):
                return (False, None)
            
            # Load template
            template = cv2.imread(img_path)
            if template is None:
                return (False, None)
            
            # OPTIMIZATION: Convert template to grayscale for 3x faster matching
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            
            # Determine which image to search in
            search_image = screenshot
            roi_offset_left = 0
            roi_offset_top = 0
            
            # If game_region is provided, extract ROI instead of scanning full screenshot
            if game_region is not None:
                try:
                    from core.utils.window_manager import get_target_bar_region
                    
                    # Get the Target HP Bar ROI
                    roi = get_target_bar_region(game_region)
                    
                    # Convert from global to screenshot-local coordinates
                    roi_local_left = roi['left'] - game_region['left']
                    roi_local_top = roi['top'] - game_region['top']
                    
                    # Validate ROI bounds
                    img_height, img_width = screenshot.shape[:2]
                    
                    # Clamp ROI to screenshot boundaries
                    roi_local_left = max(0, roi_local_left)
                    roi_local_top = max(0, roi_local_top)
                    roi_local_right = min(img_width, roi_local_left + roi['width'])
                    roi_local_bottom = min(img_height, roi_local_top + roi['height'])
                    
                    # Extract ROI from screenshot
                    if roi_local_right > roi_local_left and roi_local_bottom > roi_local_top:
                        search_image = screenshot[roi_local_top:roi_local_bottom, roi_local_left:roi_local_right]
                        roi_offset_left = roi_local_left
                        roi_offset_top = roi_local_top
                except Exception as e:
                    # Fallback to full-screen search if ROI extraction fails
                    pass
            
            # OPTIMIZATION: Convert search image to grayscale for 3x faster matching
            search_image_gray = cv2.cvtColor(search_image, cv2.COLOR_BGR2GRAY)
            
            # Perform grayscale template matching
            result = cv2.matchTemplate(search_image_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= threshold:
                # HP bar found - crop the matched region from original screenshot
                h, w = template.shape[:2]
                x, y = max_loc
                
                # Convert local coordinates back to full screenshot coordinates
                global_x = x + roi_offset_left
                global_y = y + roi_offset_top
                
                # Crop from the original screenshot (not the grayscale version)
                cropped = screenshot[global_y:global_y+h, global_x:global_x+w].copy()
                return (True, cropped)
            else:
                return (False, None)
            
        except Exception as e:
            return (False, None)

    @staticmethod
    def check_for_captcha(screenshot: np.ndarray, threshold: float = 0.55) -> Tuple[bool, Optional[Dict[str, int]]]:
        """
        Detect if CAPTCHA pop-up is visible using template matching.
        Uses assets/captcha.png as the trigger image.
        
        Returns:
            Tuple[bool, Dict] where:
            - bool: True if CAPTCHA detected, False otherwise
            - Dict: Bounding box {'left': x, 'top': y, 'width': w, 'height': h} or None
        """
        try:
            # Load the CAPTCHA trigger template
            captcha_path = resource_path(os.path.join("assets", "captcha.png"))
            
            if not os.path.exists(captcha_path):
                print("[Vision] ⚠️ captcha.png not found in assets")
                return (False, None)
            
            template = cv2.imread(captcha_path)
            if template is None:
                print("[Vision] ⚠️ Failed to load captcha.png")
                return (False, None)
            
            # Template matching
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= threshold:
                # CAPTCHA found - return bounding box
                h, w = template.shape[:2]
                x, y = max_loc
                
                captcha_rect = {
                    'left': x,
                    'top': y,
                    'width': w,
                    'height': h
                }
                return (True, captcha_rect)
            else:
                return (False, None)
        
        except Exception as e:
            print(f"[Vision] ❌ CAPTCHA detection error: {e}")
            return (False, None)

    @staticmethod
    def find_onayla_button(screenshot: np.ndarray, threshold: float = 0.7) -> Tuple[bool, Optional[Dict[str, int]]]:
        """
        Detect the confirmation button (ONAYLA) using template matching.
        Uses assets/onayla.png as the template.
        
        Returns:
            Tuple[bool, Dict] where:
            - bool: True if button found, False otherwise
            - Dict: Button bounding box {'left': x, 'top': y, 'width': w, 'height': h, 'center_x': cx, 'center_y': cy} or None
        """
        try:
            # Load the ONAYLA button template
            button_path = resource_path(os.path.join("assets", "onayla.png"))
            
            if not os.path.exists(button_path):
                print("[Vision] ⚠️ onayla.png not found in assets")
                return (False, None)
            
            template = cv2.imread(button_path)
            if template is None:
                print("[Vision] ⚠️ Failed to load onayla.png")
                return (False, None)
            
            # Template matching
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= threshold:
                # Button found - return bounding box and center
                h, w = template.shape[:2]
                x, y = max_loc
                
                button_rect = {
                    'left': x,
                    'top': y,
                    'width': w,
                    'height': h,
                    'center_x': x + w // 2,
                    'center_y': y + h // 2
                }
                
                return (True, button_rect)
            else:
                return (False, None)
        
        except Exception as e:
            print(f"[Vision] ❌ ONAYLA button detection error: {e}")
            return (False, None)

    @staticmethod
    def get_captcha_grid_image(captcha_rect: Dict[str, int], screenshot: np.ndarray, temp_dir: str = "temp") -> Optional[Tuple[str, Tuple[int, int]]]:
        """
        Extract calibrated CAPTCHA dialog using captcha header anchor.
        
        Logic:
        1. Top Anchor: "Bot Kontrol" Header (captcha_rect)
        2. Bottom Anchor: "Onayla" Button (detected dynamically)
        3. Grid is the square region sandwiched between them.
        
        Args:
            captcha_rect: Bounding box of the CAPTCHA header
            screenshot: Full screenshot image (BGR format)
            temp_dir: Directory to save temporary images
        """
        try:
            # Create temp directory
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
            
            # 1. Top Anchor (Header)
            header_x = captcha_rect['left']
            header_y = captcha_rect['top']
            header_w = captcha_rect['width']
            header_h = captcha_rect['height']
            header_center_x = header_x + (header_w // 2)
            
            # 2. Find Bottom Anchor (Onayla Button)
            button_rect = None
            try:
                button_path = resource_path(os.path.join("assets", "onayla.png"))
                if os.path.exists(button_path):
                    button_template = cv2.imread(button_path)
                    if button_template is not None:
                        # Define search region: Below header, slightly wider
                        search_y_start = header_y + header_h
                        search_y_end = screenshot.shape[0]
                        search_x_start = max(0, header_x - 100)
                        search_x_end = min(screenshot.shape[1], header_x + header_w + 100)
                        
                        if search_y_end > search_y_start and search_x_end > search_x_start:
                            search_roi = screenshot[search_y_start:search_y_end, search_x_start:search_x_end]
                            
                            # Template matching for button
                            res = cv2.matchTemplate(search_roi, button_template, cv2.TM_CCOEFF_NORMED)
                            _, max_val, _, max_loc = cv2.minMaxLoc(res)
                            
                            if max_val >= 0.6: # Slightly lower threshold for robustness
                                bx, by = max_loc
                                button_rect = {
                                    'left': search_x_start + bx,
                                    'top': search_y_start + by,
                                    'width': button_template.shape[1],
                                    'height': button_template.shape[0]
                                }
            except Exception as e:
                pass

            # 3. Calculate dialog region with calibrated geometry.
            GRID_WIDTH = Vision.CAPTCHA_DIALOG_WIDTH
            GRID_HEIGHT = Vision.CAPTCHA_DIALOG_HEIGHT
            TOP_OFFSET = Vision.CAPTCHA_DIALOG_TOP_OFFSET
            
            # Center horizontally relative to the header center
            grid_start_x = header_center_x - (GRID_WIDTH // 2)
            
            # Vertical origin is calibrated relative to header top.
            grid_start_y = header_y + TOP_OFFSET
            
            grid_width = GRID_WIDTH
            grid_height = GRID_HEIGHT

            # Boundary Checks
            if grid_start_x < 0: grid_start_x = 0
            if grid_start_y < 0: grid_start_y = 0
            
            # Crop the grid region
            grid_region = screenshot[grid_start_y:grid_start_y+grid_height, grid_start_x:grid_start_x+grid_width].copy()
            
            if grid_region.size == 0:
                print(f"[Vision] ❌ Failed to crop CAPTCHA grid (region empty)")
                return None
            
            # Verify dimensions
            actual_height, actual_width = grid_region.shape[:2]
            print(f"[Vision] ✅ CAPTCHA grid cropped: {actual_width}x{actual_height}")
            
            # Save the grid image for Gemini API
            grid_path = os.path.join(temp_dir, "combined_captcha_grid.png")
            success = cv2.imwrite(grid_path, grid_region)
            
            if not success:
                print("[Vision] ❌ Failed to save grid image")
                return None
            
            print(f"[Vision] ✅ Grid saved to {grid_path}")
            
            # Save debug view (what Gemini will analyze)
            debug_path = os.path.join(temp_dir, "gemini_debug_view.png")
            cv2.imwrite(debug_path, grid_region)
            
            # CREATE DEBUG OVERLAY: Draw rectangles on full screenshot
            debug_overlay = screenshot.copy()
            
            # 1. Header (Blue)
            cv2.rectangle(debug_overlay, (header_x, header_y), (header_x + header_w, header_y + header_h), (255, 0, 0), 2)
            cv2.putText(debug_overlay, "Top Anchor", (header_x, header_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            
            # 2. Button (Red) - if found
            if button_rect:
                bx, by, bw, bh = button_rect['left'], button_rect['top'], button_rect['width'], button_rect['height']
                cv2.rectangle(debug_overlay, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
                cv2.putText(debug_overlay, "Bottom Anchor", (bx, by + bh + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            
            # 3. Grid (Green)
            cv2.rectangle(debug_overlay, (grid_start_x, grid_start_y), (grid_start_x + grid_width, grid_start_y + grid_height), (0, 255, 0), 3)
            cv2.putText(debug_overlay, f"Grid {grid_width}x{grid_height}", (grid_start_x, grid_start_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            
            overlay_path = os.path.join(temp_dir, "debug_crop_overlay.jpg")
            overlay_success = cv2.imwrite(overlay_path, debug_overlay)
            if overlay_success:
                print(f"[Vision] ✅ Debug overlay saved to {overlay_path}")
            else:
                print(f"[Vision] ⚠️ Failed to save debug overlay")
            
            return (grid_path, (grid_start_x, grid_start_y))
        
        except Exception as e:
            print(f"[Vision] ❌ Error extracting CAPTCHA grid: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    # Deprecated: Use get_captcha_grid_image instead
    @staticmethod
    def segment_captcha_grid(captcha_rect: Dict[str, int], screenshot: np.ndarray, temp_dir: str = "temp"):
        """
        DEPRECATED: Use get_captcha_grid_image instead.
        This method is kept for backward compatibility but redirects to the new method.
        """
        result = Vision.get_captcha_grid_image(captcha_rect, screenshot, temp_dir)
        if result is not None:
            grid_path, (grid_x, grid_y) = result
            # Return old format for compatibility (path and dummy coordinates)
            width = captcha_rect.get('width', 300)
            height = captcha_rect.get('height', 300)
            cell_width = width // 3
            cell_height = height // 3
            
            cell_coords = []
            for row in range(3):
                for col in range(3):
                    cx = grid_x + col * cell_width + cell_width // 2
                    cy = grid_y + row * cell_height + cell_height // 2
                    cell_coords.append((cx, cy))
            
            return (grid_path, cell_coords)
        return None
    
    @staticmethod
    def check_if_dead(screenshot: np.ndarray, game_region: Optional[Dict[str, int]] = None, threshold: float = 0.6) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """
        Check if the character is dead by looking for the revive button/dialog.
        Uses assets/dead.png.
        
        Optimization: Scans the FULL SCREEN to ensure we don't miss it.
        
        Returns:
            Tuple[bool, Optional[Tuple[int, int]]]
            - bool: True if dead dialog found
            - Tuple: (local_x, local_y) center coordinates relative to the GAME WINDOW
        """
        try:
            dead_path = resource_path(os.path.join("assets", "dead.png"))
            # print(f"[Vision] DEBUG: Checking dead.png at {dead_path}")
            
            if not os.path.exists(dead_path):
                print(f"[Vision] ❌ Asset not found: {dead_path}")
                return (False, None)
            
            template = cv2.imread(dead_path)
            if template is None:
                print(f"[Vision] ❌ Failed to load asset: {dead_path}")
                return (False, None)
            
            # Scan Full Image (No ROI cropping for maximum safety)
            search_image = screenshot.copy()

            # Template Matching
            result = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            # print(f"[Vision] Dead detection confidence: {max_val:.2f}")
            
            if max_val >= threshold:
                # Found!
                th, tw = template.shape[:2]
                lx, ly = max_loc
                
                center_x = lx + tw // 2
                center_y = ly + th // 2
                return (True, (center_x, center_y))
            
            return (False, None)
            
        except Exception as e:
            print(f"[Vision] ❌ Dead detection error: {e}")
            return (False, None)
