import time
import threading
import random
import math
import numpy as np
import cv2
import pydirectinput
import keyboard
import os
import asyncio
from typing import Optional, Callable, Tuple, Dict
from enum import Enum
from core.drivers.driver_bot import DriverBot
from core.vision.vision import Vision
from core.utils.window_manager import get_game_region
from core.utils.path_util import resource_path
from core.ai.gemini_client import DEFAULT_GEMINI_MODEL, solve_captcha_with_gemini
from core.vision_ai import YoloVision
from core.window_capture import WindowCapture
from core.skill_manager import SkillManager
from core.inventory_manager import InventoryManager
from core.mount_checker import MountChecker
from core.process_manager import get_process_manager
from core.quest_handler import QuestHandler

# ===== PRODUCTION MODE =====
# Set to True to enable debug outputs (cv2.imshow, snapshot saving, verbose logs)
DEBUG_MODE = False


class BotState(Enum):
    """Visual State Machine States"""
    SEARCHING = "searching"
    SOLVING_CAPTCHA = "solving_captcha"
    MOVING_TO_TARGET = "moving_to_target"  # Anti-stuck strafing during movement
    VERIFY_ATTACK = "verify_attack"
    COMBAT = "combat"
    QUEUE_WAIT = "queue_wait"
    LOOT = "loot"


class BotEngine:
    SESSION_WORK_MIN_SEC = 3600.0
    SESSION_WORK_MAX_SEC = 7200.0
    SESSION_BREAK_MIN_SEC = 300.0
    SESSION_BREAK_MAX_SEC = 600.0
    DEFERRED_QUEUE_CLICK_DELAY_SEC = 3.0

    def __init__(self):
        self.driver = None
        self.stop_event = None
        self.log_callback = None
        self.signals = None
        self.overlay_callback = None
        self.stats_callback = None
        self.status_callback = None
        self.config = {}
        # MSS Capture (Stable, Cross-Platform)
        self.capturer = None
        # self.selected_map_mask is no longer needed for AI
        self.vision = YoloVision()  # Initialize YOLOv8 Vision
        self.skill_manager = SkillManager()  # Visual Buff Detection
        self.inventory_manager = InventoryManager()  # Auto-Equipment System
        self.mount_checker = MountChecker()  # CV-Based Mount Detection
        self.quest_handler = QuestHandler()  # Auto Mission Book System
        self.process_manager = get_process_manager()
        # OCR coordinate reading removed - feature deprecated
        self.state = BotState.SEARCHING
        self.state_start_time = time.time()
        self.combat_timeout = 60  # Default timeout
        self.last_log_time = 0  # For combat logging rate limiting
        self.captcha_active = False  # CAPTCHA state flag
        self.last_skill_check = 0  # Reactive skill check timer (5s interval)
        self.skill_check_interval = 7.0  # Configurable buff check interval
        self.loot_enabled = True  # Auto-pickup toggle (synced with GUI)
        self.last_strafe_time = 0  # Anti-stuck: last random strafe timestamp
        self.movement_start_time = 0  # Track when movement to target started
        self.yolo_confidence = 0.45  # YOLO confidence threshold (synced with GUI)
        self.auto_equip_enabled = True  # Toggle for auto-equipment feature (ENABLED by default)
        self.last_equip_check = 0  # Auto-equip check timer
        self.quest_enabled = False  # Toggle for auto mission book feature (DISABLED by default)
        self.last_quest_check = 0  # Quest check timer (every 3 seconds)
        self.quest_check_interval = 3.0  # Check for quest notification every 3 seconds
        self.miss_timeout = 1.5  # Stop-on-miss timeout while moving to target
        self.movement_timeout = 8.0  # Hard timeout for movement state
        self.verify_timeout = 3.0  # Max verification time before skipping target
        self.strafe_start_delay = 2.0  # Delay before combat strafing starts
        self.strafe_interval = 1.0  # Interval between combat strafes
        self.post_hp_antistuck_max_total = 2.0
        self.post_hp_antistuck_min_key_hold = 0.08
        self.post_hp_antistuck_max_key_hold = 0.65
        self.multi_target_queue_enabled = False
        self.multi_target_queue_size = 3
        self.queued_targets_count = 0
        self._deferred_queue_remaining = 0  # Phase 2: HP bar sonrası tıklanacak hedef sayısı
        self._deferred_queue_click_at = 0.0
        self.queue_wait_until = 0.0
        self.current_mouse_id = 11
        self.no_hp_click_failures = 0
        self.hp_miss_recalibrate_threshold = 5
        self.last_recalibration_attempt = 0.0
        self.recalibration_cooldown = 8.0
        self.global_captcha_cfg = {
            "enabled": True,
            "api_key": "",
            "selected_model": DEFAULT_GEMINI_MODEL,
        }

        # Circadian macro-break session manager
        self.session_start_time = time.time()
        self.next_break_threshold = 0.0
        self.is_on_break = False
        self.break_started_at = 0.0
        self.break_duration = 0.0
        self._last_break_status_emit_at = 0.0
        self._last_break_minutes_left = -1
        self._last_session_status_message = ""
        
        # Concurrency Lock
        self.is_performing_action = False  # Lock to prevent movement during skill casting

    def _refresh_global_captcha_config(self) -> None:
        defaults = {
            "enabled": True,
            "api_key": "",
            "selected_model": DEFAULT_GEMINI_MODEL,
        }

        try:
            from core.config_manager import ConfigManager

            cfg = ConfigManager("config.json").config
            global_cfg = cfg.get("global", {}) if isinstance(cfg, dict) else {}
            captcha_cfg = global_cfg.get("captcha", {}) if isinstance(global_cfg, dict) else {}
            if isinstance(captcha_cfg, dict):
                defaults["enabled"] = bool(captcha_cfg.get("enabled", True))
                defaults["api_key"] = str(captcha_cfg.get("api_key", ""))
                selected_model = str(captcha_cfg.get("selected_model", DEFAULT_GEMINI_MODEL)).strip()
                defaults["selected_model"] = selected_model or DEFAULT_GEMINI_MODEL
        except Exception:
            pass

        self.global_captcha_cfg = defaults

    def _captcha_pick_local_click_point(
        self,
        captcha_rect: Dict[str, int],
        image_index: int,
        frame_shape: Tuple[int, ...],
        grid_origin: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Map 1..6 captcha index into a local screenshot click point.

        Primary path uses `grid_origin` returned by `Vision.get_captcha_grid_image`
        so click mapping is always aligned with the exact crop sent to Gemini.
        """
        resolved_grid_origin = grid_origin
        if resolved_grid_origin is None:
            header_left = int(captcha_rect.get("left", 0))
            header_top = int(captcha_rect.get("top", 0))
            header_width = int(captcha_rect.get("width", 0))
            header_center_x = header_left + (header_width / 2.0)
            dialog_left = int(header_center_x - (Vision.CAPTCHA_DIALOG_WIDTH / 2.0))
            dialog_top = int(header_top + Vision.CAPTCHA_DIALOG_TOP_OFFSET)
            resolved_grid_origin = (dialog_left, dialog_top)

        return Vision.get_captcha_click_point(
            grid_origin=resolved_grid_origin,
            image_index=image_index,
            frame_shape=frame_shape,
        )
    
    def set_yolo_confidence(self, threshold: float):
        """
        Set YOLO detection confidence threshold.
        
        This is the CRITICAL FIX for the confidence gate bug.
        The threshold is passed directly to YoloVision for strict filtering.
        
        Args:
            threshold: Confidence threshold (0.0 to 1.0)
        """
        self.yolo_confidence = threshold
        self.vision.set_confidence_threshold(threshold)
        if self.log_callback:
            self.log_callback(f"[YOLO] Confidence threshold set to: {threshold:.0%}")

    def log(self, message: str):
        """Log message with timestamp"""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def _persist_runtime_mouse_id(self, mouse_id: int) -> None:
        try:
            from core.config_manager import ConfigManager
            cfg = ConfigManager("config.json")
            cfg.set("system", "mouse_id", int(mouse_id))
            self.log(f"[MOUSE] Mouse ID config'e kaydedildi: {int(mouse_id)}")
        except Exception as e:
            self.log(f"[MOUSE] [WARN] Mouse ID config kaydi basarisiz: {e}")

    def _attempt_runtime_mouse_recalibration(self, reason: str) -> bool:
        now = time.time()
        if now - self.last_recalibration_attempt < self.recalibration_cooldown:
            return False

        self.last_recalibration_attempt = now
        self.no_hp_click_failures = 0

        if not self.driver:
            self.log("[MOUSE] [WARN] Driver yok, runtime kalibrasyon atlandi")
            return False

        self.log(f"[MOUSE] HP miss limiti asildi. Mouse ID yeniden kalibre ediliyor... ({reason})")

        self.is_performing_action = True
        success = False
        try:
            success = bool(self.driver.reconnect(reason=reason))
            if success:
                new_id = int(getattr(self.driver, "mouse_id", self.current_mouse_id))
                self.current_mouse_id = new_id
                self._persist_runtime_mouse_id(new_id)
                self.log(f"[MOUSE] Runtime recalibration tamamlandi. Yeni Mouse ID: {new_id}")
            else:
                self.log("[MOUSE] [WARN] Runtime recalibration basarisiz")
        except Exception as e:
            self.log(f"[MOUSE] [ERROR] Runtime recalibration hatasi: {e}")
        finally:
            self.is_performing_action = False

        return success

    def _register_no_hp_failure(self, reason: str) -> None:
        self.no_hp_click_failures += 1
        self.log(
            f"[MISS] HP bar gorunmedi ({self.no_hp_click_failures}/{self.hp_miss_recalibrate_threshold})."
        )

        if self.no_hp_click_failures >= self.hp_miss_recalibrate_threshold:
            self._attempt_runtime_mouse_recalibration(reason=reason)

    def _emit_overlay(self, detections: list):
        """Thread-safe overlay update bridge (signal-first, callback fallback)."""
        try:
            if self.signals and hasattr(self.signals, 'update_overlay'):
                self.signals.update_overlay.emit(detections)
                return
        except Exception:
            pass

        if hasattr(self, 'overlay_callback') and self.overlay_callback:
            try:
                self.overlay_callback(detections)
            except Exception:
                pass

    def smart_sleep(self, duration: float):
        """Sleep with F12 failsafe check"""
        if self.stop_event is None:
            time.sleep(duration)
            return

        start = time.time()
        while time.time() - start < duration:
            if self.stop_event.is_set() or keyboard.is_pressed('F12'):
                raise KeyboardInterrupt("Failsafe triggered (F12)")
            time.sleep(0.01)  # Check every 10ms

    def _reset_session_timer(self) -> None:
        self.next_break_threshold = random.uniform(self.SESSION_WORK_MIN_SEC, self.SESSION_WORK_MAX_SEC)
        self.session_start_time = time.time()

    def _reset_break_timer(self) -> None:
        self.break_duration = random.uniform(self.SESSION_BREAK_MIN_SEC, self.SESSION_BREAK_MAX_SEC)
        self.break_started_at = time.time()

    def _emit_session_status(self, message: str, force: bool = False) -> None:
        text = str(message)
        if not force and text == self._last_session_status_message:
            return

        self._last_session_status_message = text

        try:
            if self.signals and hasattr(self.signals, "update_stat"):
                self.signals.update_stat.emit("Status", text)
        except Exception:
            pass

        if self.status_callback:
            try:
                self.status_callback(text)
            except Exception:
                pass

    def _release_inputs_for_break(self) -> None:
        if self.driver:
            try:
                self.driver.release_all_inputs()
            except Exception:
                pass

        for key_name in ("w", "a", "s", "d", "q", "z", "space", "1"):
            try:
                pydirectinput.keyUp(key_name)
            except Exception:
                pass

    def _update_session_manager(self, current_time: float) -> Tuple[bool, float]:
        if self.next_break_threshold <= 0.0:
            self._reset_session_timer()

        if self.is_on_break:
            break_end = self.break_started_at + self.break_duration
            remaining = break_end - current_time
            if remaining <= 0.0:
                self.is_on_break = False
                self._reset_session_timer()
                self.log("[SECURITY] Random break finished. Resuming work block.")
                self._emit_session_status("Status: Running...", force=True)
                return False, 0.0

            minutes_left = max(1, int(math.ceil(remaining / 60.0)))
            should_emit_status = (
                self._last_break_status_emit_at <= 0.0
                or (current_time - self._last_break_status_emit_at) >= 1.0
                or minutes_left != self._last_break_minutes_left
            )
            if should_emit_status:
                self._last_break_status_emit_at = current_time
                self._last_break_minutes_left = minutes_left
                self._emit_session_status(f"Status: ON BREAK ({minutes_left} mins left)")

            return True, min(0.5, max(0.1, remaining))

        elapsed = current_time - self.session_start_time
        if elapsed <= self.next_break_threshold:
            return False, 0.0

        self.is_on_break = True
        self._reset_break_timer()
        self._last_break_status_emit_at = 0.0
        self._last_break_minutes_left = -1
        self._release_inputs_for_break()

        self.log("[SECURITY] Work block finished. Taking a random break...")
        minutes_left = max(1, int(math.ceil(self.break_duration / 60.0)))
        self._emit_session_status(f"Status: ON BREAK ({minutes_left} mins left)", force=True)
        return True, 0.25

    def _atomic_key_hold(self, key_name: str, hold_duration: float):
        """Guarantee keyUp even if failsafe interrupts during hold."""
        pydirectinput.keyDown(key_name)
        try:
            self.smart_sleep(max(0.0, hold_duration))
        finally:
            try:
                pydirectinput.keyUp(key_name)
            except Exception:
                pass

    def _atomic_ctrl_g(self, hold_duration: float = 0.05):
        """Atomic CTRL+G tap with guaranteed CTRL release."""
        pydirectinput.keyDown('ctrl')
        try:
            self.smart_sleep(max(0.0, hold_duration))
            pydirectinput.press('g')
        finally:
            try:
                pydirectinput.keyUp('ctrl')
            except Exception:
                pass

    def get_game_screenshot(self, _game_region: dict) -> Optional[np.ndarray]:
        """Capture using WindowCapture (MSS - Stable)"""
        try:
            if self.capturer is None:
                return None
            
            # Capture using MSS
            frame = self.capturer.capture_frame()
            
            if frame is None:
                # Try to reinitialize if capture failed
                if not self.capturer.sct:
                    return None
                frame = self.capturer.capture_frame()
            
            return frame
        except Exception as e:
            self.log(f"[HATA] Ekran goruntusu alinamadi: {e}")
            return None

    def find_game_window(self) -> Optional[dict]:
        """
        Resolve current game client region.

        Priority:
        1. Attached HWND/client geometry from WindowCapture (title-agnostic, robust)
        2. Legacy title-based fallback for backward compatibility
        """
        if self.capturer and self.capturer.target_hwnd:
            x, y, w, h = self.capturer.get_client_area_geometry()
            if w > 0 and h > 0:
                return {
                    'top': y,
                    'left': x,
                    'width': w,
                    'height': h
                }

        TARGET_TITLES = ["Rubinum", "Saryong", "Metin2", "Client", "Game"]
        for title in TARGET_TITLES:
            try:
                region = get_game_region(title)
                return region
            except Exception:
                continue
        return None

    def is_game_focused(self) -> bool:
        """
        ISSUE 2 FIX: Focus Safety Gate
        
        Check if the game window is currently the foreground (active) window.
        Uses WindowCapture's is_window_focused() method.
        
        Returns:
            bool: True if game window is focused, False otherwise
        """
        if self.capturer:
            return self.capturer.is_window_focused()
        return False

    def human_click(self, x: int, y: int, fast: bool = False) -> bool:
        """
        Perform single reliable click with proper timing.
        
        ISSUE 2 FIX: Includes focus safety gate - will NOT click if 
        game window is not the active foreground window.
        """
        try:
            if self.driver is None:
                self.log("[HATA] Driver hazir degil, tiklama atlandi")
                return False

            # ============================================================
            # ISSUE 2 FIX: Focus Safety Gate
            # Check if game window is focused BEFORE any click action
            # ============================================================
            if not self.is_game_focused():
                self.log("[WAIT] Game window lost focus. Pausing actions...")
                time.sleep(0.5)  # Prevent CPU spinning
                return False  # Do NOT perform click
            
            # 1. Mouse'u hedefe götür
            self.driver.move_abs(x, y)
            
            # 2. Ekranin stabilize olmasi icin bekle
            wait_time = random.uniform(0.02, 0.06) if fast else random.uniform(0.04, 0.10)
            self.smart_sleep(wait_time)
            
            # 3. Final focus check before click (window might have lost focus during wait)
            if not self.is_game_focused():
                self.log("[WAIT] Game window lost focus during move. Click aborted.")
                return False
            
            # 4. Tek tiklama (hizli gamer click: 20-40ms)
            self.driver.click(duration_ms=random.randint(20, 40))
            return True
            
        except Exception as e:
            self.log(f"[HATA] Tiklama hatasi: {e}")
            return False

    def _handle_death_protocol(self, game_region: Dict[str, int], dead_pos_local: Tuple[int, int], _wait_time: int):
        """
        AGGRESSIVE Resurrection Protocol (Anti-Spawn Kill):
        
        GOAL: Minimize Time-to-Action to avoid spawn kills!
        
        1. Stop attacking
        2. FAST WAIT (1.5s MAX) - Just enough for server sync
        3. Click "Burada yeniden başla" (Restart Here)
        4. FAST MOUNT CHECK (1.0s) - CV-based verification
        5. IMMEDIATE ACTION - Transition to SEARCH (no retreat!)
        
        Total Time: ~3s (down from ~8s)
        """
        self.log("[OLUM] ⚡ HIZLI dirilme protokolü başlatıldı...")
        
        # 1. Stop All Actions
        pydirectinput.keyUp('space')
        pydirectinput.keyUp('1')
        
        # 2. FAST PRE-CLICK WAIT (1.5s - server sync only)
        self.log("[BEKLE] Sunucu senkronizasyonu (1.5s)...")
        self.smart_sleep(1.5)
        
        # ============================================================
        # CLICK RESTART BUTTON
        # ============================================================
        template_center_x, template_center_y = dead_pos_local
        TOP_BUTTON_Y_OFFSET = -17  # Click TOP button (Restart Here)
        
        local_click_x = template_center_x
        local_click_y = template_center_y + TOP_BUTTON_Y_OFFSET
        
        # Convert to global screen coordinates
        if self.capturer and self.capturer.target_hwnd:
            global_click_x, global_click_y = self.capturer.get_screen_position(local_click_x, local_click_y)
        else:
            global_click_x = game_region['left'] + local_click_x
            global_click_y = game_region['top'] + local_click_y
        
        self.log(f"[CLICK] Restart Here: ({global_click_x}, {global_click_y})")
        if self.driver is None:
            self.log("[HATA] Driver hazir degil, dirilme tiklamasi atlandi")
            return
        self.driver.move_abs(global_click_x, global_click_y)
        self.smart_sleep(0.15)  # Short move delay
        self.driver.click(duration_ms=25)
        
        # 3. FAST ANIMATION WAIT (1.0s - just enough for respawn)
        self.log("[BEKLE] Dirilme (1.0s)...")
        self.smart_sleep(1.0)
        
        # 4. CV-BASED MOUNT CHECK (with explicit window targeting)
        try:
            self.log("[MOUNT] Binek durumu kontrol ediliyor...")
            
            # Build game region dict for MountChecker
            mount_check_region = {
                'left': game_region.get('left', 0),
                'top': game_region.get('top', 0),
                'width': game_region.get('width', 1920),
                'height': game_region.get('height', 1080)
            }
            
            is_unmounted = self.mount_checker.check_is_unmounted(
                press_key=True,
                game_region=mount_check_region
            )
            
            if is_unmounted:
                # UNMOUNTED - Need to mount NOW
                self.log("[MOUNT] ⚠️ YAYA! Bineğe biniliyor...")
                self._atomic_ctrl_g(0.05)
                self.smart_sleep(0.3)  # Short mount animation
                
                if hasattr(self, 'skill_manager'):
                    self.skill_manager.is_mounted = True
                    
                self.log("[MOUNT] ✓ Binildi!")
            else:
                # Already mounted
                self.log("[MOUNT] ✓ Zaten binekte.")
                if hasattr(self, 'skill_manager'):
                    self.skill_manager.is_mounted = True
                    
        except Exception as e:
            # Fallback: Mount anyway
            self.log(f"[MOUNT] Hata: {e} - Yedek CTRL+G...")
            self._atomic_ctrl_g(0.05)
            self.smart_sleep(0.2)
        
        # 5. NO RETREAT! Immediate action is better than getting lost
        # The bot will start moving to a target instantly, which is the best defense
        
        # 6. SKIP HP REGEN WAIT - We need to MOVE, not stand still!
        # Standing still = getting killed again
        
        self.log("[OK] ⚡ Dirilme tamamlandı! Hedef aranıyor...")
    
    def _use_skills_after_revive(self):
        """
        Dirilme sonrasi skill kullanimi.
        Pattern: Skill1 > CTRL+G > CTRL+G > Skill2 > CTRL+G
        (Karakter zaten binekten inmis vaziyette)
        """
        try:
            # 1. Skill 1 (zaten yerde, binekte degil)
            if hasattr(self, 'skill_key_1') and self.skill_key_1:
                self.log(f"[SKILL] Skill 1 ({self.skill_key_1})...")
                pydirectinput.press(self.skill_key_1)
                self.smart_sleep(0.3)
            
            # 2. CTRL+G (Ata bin - animasyon iptali)
            self._atomic_ctrl_g(0.05)
            self.smart_sleep(0.3)
            
            # 3. CTRL+G (Attan in - skill 2 icin)
            self._atomic_ctrl_g(0.05)
            self.smart_sleep(0.5)
            
            # 4. Skill 2
            if hasattr(self, 'skill_key_2') and self.skill_key_2:
                self.log(f"[SKILL] Skill 2 ({self.skill_key_2})...")
                pydirectinput.press(self.skill_key_2)
                self.smart_sleep(0.3)
            
            # 5. CTRL+G (Ata bin - final)
            self._atomic_ctrl_g(0.05)
            self.smart_sleep(0.5)
            
            self.last_skill_time = time.time()
            self.log("[OK] Dirilme sonrasi skill tamamlandi.")
            
        except Exception as e:
            self.log(f"[HATA] Dirilme skill hatasi: {e}")

    def _ensure_search_counters(self) -> None:
        if not hasattr(self, 'failed_click_count'):
            self.failed_click_count = 0
        if not hasattr(self, 'last_clicked_pos'):
            self.last_clicked_pos = None

    def _is_repeated_search_target(self, current_pos: Tuple[int, int], game_region: Dict[str, int]) -> bool:
        if not self.last_clicked_pos:
            return False

        dist = ((current_pos[0] - self.last_clicked_pos[0])**2 +
                (current_pos[1] - self.last_clicked_pos[1])**2)**0.5

        client_width = game_region.get('width', 1024)
        client_height = game_region.get('height', 768)
        min_move_distance = min(client_width, client_height) * 0.04

        if dist < min_move_distance:
            self.failed_click_count += 1
            self.log(f"[UYARI] Ayni noktaya tekrar tiklaniyor ({self.failed_click_count}) - dist: {dist:.0f} < {min_move_distance:.0f}")
            return True

        self.failed_click_count = 0
        return False

    def _handle_search_stuck(self) -> bool:
        if self.failed_click_count <= 5:
            return False

        self.log("[TAKILI] Ayni tasa cok kez tiklandi. Kamera donduruluyor...")
        self._atomic_key_hold('q', 0.4)
        self.failed_click_count = 0
        self.last_clicked_pos = None
        return True

    def _search_roam(self) -> None:
        self.log("[AI] Tas yok. Araniyor...")
        self._atomic_key_hold('q', 0.3)

    def _run_post_hp_antistuck_burst(self) -> None:
        """Run a short randomized WASD burst after HP lock to reduce local sticking."""
        max_total = max(0.0, float(getattr(self, "post_hp_antistuck_max_total", 2.0)))
        if max_total <= 0.0:
            return

        min_hold = max(0.01, float(getattr(self, "post_hp_antistuck_min_key_hold", 0.08)))
        max_hold = max(min_hold, float(getattr(self, "post_hp_antistuck_max_key_hold", 0.65)))

        keys = ["w", "a", "s", "d"]
        random.shuffle(keys)

        remaining_total = max_total
        remaining_keys = len(keys)

        for key in keys:
            reserve_for_rest = min_hold * max(0, remaining_keys - 1)
            max_for_this = min(max_hold, remaining_total - reserve_for_rest)
            if max_for_this < min_hold:
                hold = max(0.0, remaining_total - reserve_for_rest)
            else:
                hold = random.uniform(min_hold, max_for_this)

            hold = max(0.0, min(hold, remaining_total))
            if hold > 0.0:
                self._atomic_key_hold(key, hold)
                remaining_total -= hold

            remaining_keys -= 1
            if remaining_total <= 0.0:
                break

    def _get_reachable_distance_px(self) -> float:
        default_threshold = 420.0
        if not isinstance(self.config, dict):
            return default_threshold

        combat_cfg = self.config.get("combat", {})
        if not isinstance(combat_cfg, dict):
            return default_threshold

        raw_threshold = combat_cfg.get("reachable_distance_px", default_threshold)
        try:
            threshold = float(raw_threshold)
        except Exception:
            threshold = default_threshold

        if threshold <= 0.0:
            return float("inf")
        return threshold

    def _get_deferred_queue_click_delay_sec(self) -> float:
        default_delay = self.DEFERRED_QUEUE_CLICK_DELAY_SEC
        if not isinstance(self.config, dict):
            return default_delay

        combat_cfg = self.config.get("combat", {})
        if not isinstance(combat_cfg, dict):
            return default_delay

        raw_delay = combat_cfg.get("deferred_queue_click_delay_sec", default_delay)
        try:
            delay = float(raw_delay)
        except Exception:
            delay = default_delay

        return max(0.0, delay)

    def _get_mask_regions(self) -> list:
        if not isinstance(self.config, dict):
            return []

        vision_cfg = self.config.get("vision", {})
        if not isinstance(vision_cfg, dict):
            return []

        mask_regions = vision_cfg.get("mask_regions", [])
        return mask_regions if isinstance(mask_regions, list) else []

    def _target_distance_from_character_center(
        self,
        target_pos: Tuple[int, int],
        game_region: Dict[str, int],
        frame_shape: Tuple[int, ...],
    ) -> float:
        frame_h = int(frame_shape[0]) if len(frame_shape) > 0 else int(game_region.get("height", 0))
        frame_w = int(frame_shape[1]) if len(frame_shape) > 1 else int(game_region.get("width", 0))

        center_global_x = float(game_region.get("left", 0)) + (float(frame_w) * 0.5)
        center_global_y = float(game_region.get("top", 0)) + (float(frame_h) * 0.5)

        return math.hypot(float(target_pos[0]) - center_global_x, float(target_pos[1]) - center_global_y)

    def _refresh_queue_positions(self, game_region: Dict[str, int], queue_limit: int) -> list:
        """Re-detect queue targets on a fresh frame to avoid stale click coordinates."""
        if not self.capturer or not self.vision:
            return []

        fresh_img = self.get_game_screenshot(game_region)
        if fresh_img is None:
            return []

        local_targets, _ = self.vision.get_top_targets(
            img=fresh_img,
            max_targets=max(1, int(queue_limit)),
            mask_regions=self._get_mask_regions(),
        )
        refreshed_global_targets = []
        for local_target in local_targets:
            lx, ly = int(local_target[0]), int(local_target[1])
            gx, gy = self.capturer.get_screen_position(lx, ly)
            refreshed_global_targets.append((gx, gy))

        return refreshed_global_targets

    def _click_target_queue(self, queued_positions: list, game_region: Dict[str, int]) -> int:
        clicked_count = 0
        queue_plan_count = len(queued_positions)

        for idx in range(queue_plan_count):
            current_positions = queued_positions if idx == 0 else self._refresh_queue_positions(game_region, queue_plan_count)
            if len(current_positions) <= idx:
                break

            pos = current_positions[idx]
            global_x, global_y = int(pos[0]), int(pos[1])
            clicked = self.human_click(global_x, global_y)
            if clicked:
                clicked_count += 1
                if clicked_count == 1:
                    self.last_clicked_pos = (global_x, global_y)

            # Anti-ban delay between queued targets
            if idx < queue_plan_count - 1:
                self.smart_sleep(random.uniform(0.15, 0.30))

        return clicked_count

    def state_searching(self, _img_bgr: np.ndarray, game_region: dict, target_positions: Optional[list],
                        _detections: Optional[list] = None) -> Optional[BotState]:
        """
        STATE: SEARCHING
        Detect stones using YOLOv8 AI and click on them.
        
        Multi-Target Queue v2 (Deferred Click):
        - Sadece İLK hedefe tıkla (ekran kaymaya başlar)
        - Kalan hedefler MOVING_TO_TARGET'ta HP bar göründükten sonra tıklanır
        - Bu sayede ekran durağanken 2./3. tıklama isabetli olur
        
        Returns next state or None to stay in SEARCHING.
        """
        try:
            self._ensure_search_counters()

            # If target HP bar is already visible, do not click a new Metin target.
            hp_visible, _ = Vision.is_hp_bar_visible(_img_bgr, game_region=game_region, threshold=0.6)
            if hp_visible:
                self.queued_targets_count = 0
                self._deferred_queue_remaining = 0
                self._deferred_queue_click_at = 0.0
                return None

            # 1. AI Detection (Passed from Main Loop)
            queued_positions = target_positions if isinstance(target_positions, list) else []
            if queued_positions:
                first_target = queued_positions[0]
                detected_count = len(queued_positions)
                self.log(f"[AI] Metin tasi bulundu. Tespit edilen: {detected_count}")

                current_pos = (int(first_target[0]), int(first_target[1]))

                reachable_threshold_px = self._get_reachable_distance_px()
                closest_distance_px = self._target_distance_from_character_center(
                    current_pos,
                    game_region,
                    _img_bgr.shape,
                )
                if closest_distance_px > reachable_threshold_px:
                    self.queued_targets_count = 0
                    self._deferred_queue_remaining = 0
                    self._deferred_queue_click_at = 0.0
                    self.log(
                        (
                            "[AI] En yakin tas erisilebilir mesafe disinda "
                            f"(dist={closest_distance_px:.1f}px > limit={reachable_threshold_px:.1f}px)."
                        )
                    )
                    self._atomic_key_hold('q', 0.2)
                    return None

                self._is_repeated_search_target(current_pos, game_region)

                if self._handle_search_stuck():
                    return None

                # === PHASE 1: Sadece İLK hedefe tıkla ===
                global_x, global_y = int(first_target[0]), int(first_target[1])
                clicked = self.human_click(global_x, global_y)

                if not clicked:
                    self.log("[WAIT] Tiklama gerceklesmedi. Arama devam ediyor...")
                    return None

                self.last_clicked_pos = (global_x, global_y)

                # Kuyruktaki TOPLAM hedef sayısını kaydet (tıklanan 1 + bekleyen N-1)
                self.queued_targets_count = detected_count
                self._deferred_queue_remaining = detected_count - 1  # HP bar sonrası tıklanacak
                deferred_delay_sec = self._get_deferred_queue_click_delay_sec()
                if self._deferred_queue_remaining > 0:
                    self._deferred_queue_click_at = time.time() + deferred_delay_sec
                else:
                    self._deferred_queue_click_at = 0.0
                self.queue_wait_until = 0.0

                if self._deferred_queue_remaining > 0:
                    self.log(
                        f"[AI] İlk hedefe tıklandı. Kalan {self._deferred_queue_remaining} hedef "
                        f"{deferred_delay_sec:.1f}sn sonra tıklanacak."
                    )
                else:
                    self.log("[AI] Tek hedef tıklandı.")

                # Initialize movement tracking for anti-stuck
                self.movement_start_time = time.time()
                self.last_strafe_time = time.time()
                self.state_start_time = time.time()

                # Transition to MOVING_TO_TARGET (anti-stuck state)
                return BotState.MOVING_TO_TARGET

            # No stone found -> Rotate Camera / Roam
            self.queued_targets_count = 0
            self._deferred_queue_remaining = 0
            self._deferred_queue_click_at = 0.0
            self._search_roam()
                
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] AI ARAMA durumu hatasi: {e}")
            import traceback
            traceback.print_exc()
        
        return None  # Stay in SEARCHING

    def _click_deferred_queue_targets(self, game_region: Dict[str, int]) -> None:
        """
        PHASE 2: HP bar göründükten sonra kalan kuyruk hedeflerini tıkla.
        
        Ekran artık durağan olduğu için taze YOLO tespiti yapılır ve
        kalan hedefler isabetli şekilde tıklanır.
        """
        remaining = getattr(self, '_deferred_queue_remaining', 0)
        if remaining <= 0:
            self._deferred_queue_click_at = 0.0
            return

        self.log(f"[QUEUE] Ekran durağan. {remaining} kalan hedef tıklanıyor...")

        # Taze YOLO tespiti (ekran artık sabit, koordinatlar güvenilir)
        fresh_positions = self._refresh_queue_positions(game_region, remaining + 1)

        # İlk hedef zaten tıklanmış (index 0 = aktif hedef), kalan hedefleri tıkla
        clicked_extra = 0
        # İlk hedefi atla (zaten kilitli), 2. ve sonrasını tıkla
        targets_to_click = fresh_positions[1:remaining + 1] if len(fresh_positions) > 1 else []

        for idx, pos in enumerate(targets_to_click):
            global_x, global_y = int(pos[0]), int(pos[1])
            clicked = self.human_click(global_x, global_y)
            if clicked:
                clicked_extra += 1
                self.log(f"[QUEUE] Ek hedef {idx + 1}/{remaining} tıklandı: ({global_x}, {global_y})")

            # Anti-ban delay between queued targets
            if idx < len(targets_to_click) - 1:
                self.smart_sleep(random.uniform(0.12, 0.25))

        # Toplam kuyruktaki hedef sayısını güncelle (1 aktif + tıklanan ekstralar)
        self.queued_targets_count = 1 + clicked_extra
        self._deferred_queue_remaining = 0
        self._deferred_queue_click_at = 0.0
        self.log(f"[QUEUE] Kuyruk tıklaması tamamlandı. Toplam kuyruktaki hedef: {self.queued_targets_count}")

    def state_moving_to_target(self, img_bgr: np.ndarray, game_region: Dict[str, int]) -> Optional[BotState]:
        """
        STATE: MOVING_TO_TARGET (Stop-on-Miss Validation + Deferred Queue Click)
        
        Flow:
        1. Check if HP bar is visible (target locked, screen stable)
        2. If HP bar visible AND deferred queue targets exist:
           → Click remaining targets on fresh stable frame
        3. If HP bar visible: Proceed to VERIFY_ATTACK
        4. If HP bar NOT visible within miss_timeout: STOP and return to SEARCH
        
        Note: A/D strafing happens in COMBAT state, not here.
        """
        try:
            current_time = time.time()
            elapsed = current_time - self.movement_start_time
            
            # ============================================================
            # PRIORITY 0: INSTANT DEATH DETECTION
            # ============================================================
            is_dead, dead_pos = Vision.check_if_dead(img_bgr, game_region)
            if is_dead and dead_pos:
                self.log("[MOVING] OLUM TESPIT EDILDI!")
                self._pending_death = (game_region, dead_pos)
                self.queued_targets_count = 0
                self._deferred_queue_remaining = 0
                self._deferred_queue_click_at = 0.0
                self.queue_wait_until = 0.0
                return BotState.SEARCHING
            
            # --- CHECK: Is HP bar visible? (Target confirmed) ---
            hp_visible, _ = Vision.is_hp_bar_visible(img_bgr, game_region=game_region, threshold=0.6)
            
            if hp_visible:
                # ============================================================
                # TARGET CONFIRMED - HP bar visible, screen is now STABLE
                # ============================================================
                self.no_hp_click_failures = 0
                self.log("[OK] HP bar gorundu! Hedefe kilitlendi.")

                # === PHASE 2: Kalan kuyruk hedeflerini şimdi tıkla ===
                # Ekran durağan, koordinatlar güvenilir
                if getattr(self, '_deferred_queue_remaining', 0) > 0:
                    deferred_click_at = float(getattr(self, '_deferred_queue_click_at', 0.0))
                    if deferred_click_at > current_time:
                        self.smart_sleep(min(0.1, deferred_click_at - current_time))
                        return None
                    self._click_deferred_queue_targets(game_region)

                self._run_post_hp_antistuck_burst()

                self.state_start_time = time.time()
                return BotState.VERIFY_ATTACK
            
            else:
                # ============================================================
                # HP BAR NOT VISIBLE - Check for misclick
                # ============================================================
                
                # --- STOP-ON-MISS: If no HP bar within miss_timeout, abort ---
                if elapsed > self.miss_timeout:
                    self.log("[MISS] HP bar yok! Tiklama basarisiz. Durduruluyor...")
                    
                    # Press 'S' to stop character immediately
                    pydirectinput.press('s')
                    time.sleep(0.2)
                    
                    # Increment fail counter and return to searching
                    self.failed_click_count = getattr(self, 'failed_click_count', 0) + 1
                    self._register_no_hp_failure(reason="moving_to_target_hp_timeout")
                    self.log(f"[SEARCH] Yeni hedef araniyor... (Basarisiz: {self.failed_click_count})")
                    self.queued_targets_count = 0
                    self._deferred_queue_remaining = 0
                    self._deferred_queue_click_at = 0.0
                    self.queue_wait_until = 0.0
                    
                    return BotState.SEARCHING
                
                # Still waiting for HP bar to appear (within timeout window)
                time.sleep(0.1)
            
            # --- HARD TIMEOUT: Movement took way too long ---
            if elapsed > self.movement_timeout:
                self.log("[TIMEOUT] Hareket cok uzun surdu. Durduruluyor...")
                pydirectinput.press('s')
                time.sleep(0.2)
                self.queued_targets_count = 0
                self._deferred_queue_remaining = 0
                self._deferred_queue_click_at = 0.0
                self.queue_wait_until = 0.0
                return BotState.SEARCHING
            
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] HAREKET durumu hatasi: {e}")
            pydirectinput.press('s')  # Safety stop on error
            self._deferred_queue_remaining = 0
            self._deferred_queue_click_at = 0.0
            return BotState.SEARCHING
        
        return None  # Stay in MOVING_TO_TARGET

    def state_verify_attack(self, img_bgr: np.ndarray, game_region: Dict[str, int]) -> Optional[BotState]:  # NOSONAR
        """
        STATE: VERIFY_ATTACK
        Tiklama sonrasi HP bar kontrolu yapar.
        HP bar varsa -> COMBAT (tiklama basarili)
        HP bar yoksa -> 2. deneme yap, yine basarisizsa tasi atla
        """
        try:
            # ============================================================
            # PRIORITY 0: INSTANT DEATH DETECTION
            # ============================================================
            is_dead, dead_pos = Vision.check_if_dead(img_bgr, game_region)
            if is_dead and dead_pos:
                self.log("[VERIFY] OLUM TESPIT EDILDI!")
                self._pending_death = (game_region, dead_pos)
                self.queued_targets_count = 0
                self.queue_wait_until = 0.0
                return BotState.SEARCHING
            
            # HP bar kontrolu - dusuk threshold ile hassas algilama
            found, _ = Vision.is_hp_bar_visible(img_bgr, game_region=game_region, threshold=0.6)
            
            if found:
                # HP bar gorunuyor = tiklama basarili
                self.no_hp_click_failures = 0
                self.log("[OK] Tiklama basarili! HP bar gorunuyor. Savas basliyor...")
                self.state_start_time = time.time()
                self.last_log_time = time.time()
                self.hp_not_found_count = 0
                self.hp_was_visible = True
                self.failed_click_count = 0  # Basarili tiklama, sayaci sifirla
                return BotState.COMBAT
            
            # HP bar bulunamadi - timeout kontrol et
            elapsed = time.time() - self.state_start_time
            if elapsed > self.verify_timeout:
                # 3 saniye gecti HP bar hala yok = tiklama basarisiz
                if not hasattr(self, 'failed_click_count'):
                    self.failed_click_count = 0
                
                self.failed_click_count += 1
                self._register_no_hp_failure(reason="verify_attack_hp_timeout")
                
                if self.failed_click_count >= 2:
                    # 2 kez basarisiz oldu, bu tasi atla
                    self.log("[ATLA] Ayni tasa 2 kez tiklandi, HP bar yok. Tas atlaniyor...")
                    if hasattr(self, 'last_clicked_pos') and self.last_clicked_pos:
                        if not hasattr(self, 'skipped_positions'):
                            self.skipped_positions = []
                        self.skipped_positions.append(self.last_clicked_pos)
                    self.failed_click_count = 0
                else:
                    self.log(f"[BASARISIZ] HP bar bulunamadi ({self.failed_click_count}/2). Tekrar deneniyor...")

                self.queued_targets_count = 0
                self.queue_wait_until = 0.0
                
                return BotState.SEARCHING
            
            # Kisa aralikla kontrol
            self.smart_sleep(0.15)
            
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] DOGRULAMA durumu hatasi: {e}")
        
        return None  # Stay in VERIFY_ATTACK

    def state_combat(self, _img_bgr: np.ndarray, game_region: Dict[str, int]) -> Optional[BotState]:  # NOSONAR
        """
        STATE: COMBAT (Zero-Latency Mode + Anti-Stuck Strafing)
        Prioritized Loop:
        1. EXIT check (HP Bar) - Break immediately if dead.
        2. LOCK check (Skills) - Skip movement if busy.
        3. ACTION check (Strafe) - Perform anti-stuck.
        """
        try:
            self.log("[COMBAT] Savas dongusu basladi (Refactored)...")
            start_combat = time.time()
            last_vision_update = 0
            hp_missing_count = 0
            last_z_press = 0
            last_strafe_time = start_combat
            last_buff_check = start_combat
            strafe_direction = 'a'  # Alternating pattern: starts with 'a', then 'd', then 'a'...
            
            while True:
                # 0. Check Stop
                if self.stop_event and self.stop_event.is_set():
                    return None
                    
                # 1. Capture Fresh Screenshot
                current_img = self.get_game_screenshot(game_region)
                if current_img is None:
                    time.sleep(0.05)
                    continue

                # ============================================================
                # PRIORITY 0: INSTANT DEATH DETECTION
                # Must be checked BEFORE HP bar to catch death immediately
                # ============================================================
                is_dead, dead_pos = Vision.check_if_dead(current_img, game_region)
                if is_dead and dead_pos:
                    self.log("[COMBAT] OLUM TESPIT EDILDI! Dirilme protokolune geciliyor...")
                    # Store death info for main loop to handle
                    self._pending_death = (game_region, dead_pos)
                    self.queued_targets_count = 0
                    self.queue_wait_until = 0.0
                    return BotState.SEARCHING  # Exit combat, main loop will handle death

                # 2. Check HP Bar (Priority 1: The Exit)
                found, _ = Vision.is_hp_bar_visible(current_img, game_region=game_region, threshold=0.6)
                
                # --- VISUAL UPDATE ---
                if time.time() - last_vision_update > 1.0:
                    try:
                        _, detections = self.vision.get_closest_stone(img=current_img)
                        # Keep detections in LOCAL client coordinates for overlay.
                        # Overlay handles DPI/client scaling on its side.
                        if hasattr(self, 'overlay_callback') and self.overlay_callback and self.capturer:
                            overlay_dets = []
                            for det in detections:
                                local_rect = det.get('rect', [])
                                if len(local_rect) == 4:
                                    overlay_dets.append({
                                        'rect': [
                                            float(local_rect[0]), float(local_rect[1]),
                                            float(local_rect[2]), float(local_rect[3])
                                        ],
                                        'label': det.get('label', 'metin'),
                                        'conf': det.get('conf', 0.0)
                                    })
                            self.overlay_callback(overlay_dets)
                        last_vision_update = time.time()
                    except Exception:
                        pass
                
                if not found:
                    # HP Bar not found
                    hp_missing_count += 1
                    
                    # DEBOUNCE CHECK
                    if hp_missing_count < 5:
                        time.sleep(0.03)
                        continue
                        
                    # --- TARGET DESTROYED ---
                    self.log("[COMBAT] HP Bar kayboldu! Tas yok edildi.")

                    # Count kill first for both normal and queued flow.
                    if hasattr(self, 'stones_destroyed'):
                        self.stones_destroyed += 1
                        total = self.stones_destroyed
                    else:
                        self.stones_destroyed = 1
                        total = 1

                    # Multi-target queue handoff: wait for next queued target HP bar.
                    if self.queued_targets_count > 1:
                        self.queued_targets_count -= 1
                        self.queue_wait_until = time.time() + 1.5

                        if hasattr(self, 'stats_callback') and self.stats_callback:
                            elapsed = time.time() - self.bot_start_time if hasattr(self, 'bot_start_time') else 0
                            self.stats_callback(total, elapsed)

                        self.failed_click_count = 0
                        self.last_clicked_pos = None
                        self.skipped_positions = []
                        self.log(
                            f"[QUEUE] Hedef kirildi. Siradaki kuyruk hedefi bekleniyor "
                            f"(kalan: {self.queued_targets_count})"
                        )
                        return BotState.QUEUE_WAIT
                    
                    # Ultra-fast loot spam (only if enabled)
                    if self.loot_enabled:
                        pydirectinput.press('z', presses=8, interval=0.01)

                    self.queued_targets_count = 0
                    self.queue_wait_until = 0.0
                        
                    self.log(f"[OK] Tas kirildi! (Toplam: {total}) - Yeni tas araniyor...")
                    
                    # GUI Update
                    if hasattr(self, 'stats_callback') and self.stats_callback:
                        elapsed = time.time() - self.bot_start_time if hasattr(self, 'bot_start_time') else 0
                        self.stats_callback(total, elapsed)
                    
                    # Reset State Checkers
                    self.failed_click_count = 0
                    self.last_clicked_pos = None
                    self.skipped_positions = []
                    
                    return BotState.SEARCHING
                
                # --- TARGET IS ALIVE (HP Bar Visible) ---
                hp_missing_count = 0
                
                # --- Priority 2: The Lock (Buffs & Actions) ---
                # Check Buffs Check (Updates self.is_performing_action)
                if hasattr(self, 'skill_manager') and hasattr(self, 'auto_skill_enabled') and self.auto_skill_enabled:
                     if time.time() - last_buff_check >= self.skill_check_interval:
                         self.is_performing_action = True
                         try:
                             self.skill_manager.check_and_refresh_in_combat(current_img, log_callback=self.log)
                             last_buff_check = time.time()
                         finally:
                             self.is_performing_action = False
                
                # Check Lock
                if self.is_performing_action:
                    time.sleep(0.03)
                    continue

                # --- Priority 3: The Action (Strafe) ---
                # Start strafing after 2s, then every ~1s with short key presses
                combat_elapsed = time.time() - start_combat
                if combat_elapsed > self.strafe_start_delay:
                    if time.time() - last_strafe_time >= self.strafe_interval:
                        # Alternating pattern (A, D, A, D...)
                        strafe_key = strafe_direction
                        strafe_direction = 'd' if strafe_direction == 'a' else 'a'  # Toggle for next time
                        
                        strafe_duration = 0.3  # Short key press
                        
                        self.log(f"[ANTI-STUCK] Combat strafe '{strafe_key.upper()}'")

                        self._atomic_key_hold(strafe_key, strafe_duration)
                        
                        last_strafe_time = time.time()

                # Timeout / Stuck Checks
                if time.time() - start_combat > self.combat_timeout:
                    self.log(f"[TIMEOUT] Tas {self.combat_timeout}sn icinde kirilmadi. Takili kalmis olabilir.")
                    pydirectinput.press('s', presses=3, interval=0.1)
                    pydirectinput.press('d', presses=3, interval=0.1)
                    self.queued_targets_count = 0
                    self.queue_wait_until = 0.0
                    return BotState.SEARCHING
                
                # Passive Loot
                if self.loot_enabled and (time.time() - last_z_press > 1.0):
                    pydirectinput.press('z')
                    last_z_press = time.time()

                time.sleep(0.03)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] COMBAT loop hatasi: {e}")
            import traceback
            traceback.print_exc()
        
        return BotState.SEARCHING

    def state_queue_wait(self, img_bgr: np.ndarray, game_region: Dict[str, int]) -> Optional[BotState]:
        """
        STATE: QUEUE_WAIT
        Wait up to 1.5s for the next queued target's HP bar to appear.
        """
        try:
            hp_visible, _ = Vision.is_hp_bar_visible(img_bgr, game_region=game_region, threshold=0.6)

            if hp_visible:
                self.log(f"[QUEUE] Siradaki hedefe kilitlenildi (kalan: {self.queued_targets_count})")
                self.queue_wait_until = 0.0
                self.state_start_time = time.time()
                return BotState.COMBAT

            if time.time() >= self.queue_wait_until:
                self.log("[QUEUE] 1.5sn grace suresi doldu, yeni hedef araniyor...")
                self.queued_targets_count = 0
                self.queue_wait_until = 0.0
                return BotState.SEARCHING

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] QUEUE_WAIT durumu hatasi: {e}")
            self.queued_targets_count = 0
            self.queue_wait_until = 0.0
            return BotState.SEARCHING

        return None

    def state_loot(self) -> Optional[BotState]:
        """
        STATE: LOOT - Hizli loot toplama (artik combat'ta yapiliyor)
        Bu state sadece fallback olarak kalir.
        """
        try:
            # Hizli Z spam ve direkt yeni tasa gec (only if enabled)
            if self.loot_enabled:
                pydirectinput.press('z', presses=10, interval=0.02)
            self.queued_targets_count = 0
            self.queue_wait_until = 0.0
            self.log("[OK] Yeni tas araniyor...")
            return BotState.SEARCHING
            
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.log(f"[HATA] LOOT durumu hatasi: {e}")
            return BotState.SEARCHING

    def _run_non_critical_tasks(self, img_bgr: np.ndarray, game_region: Dict[str, int], current_time: float) -> None:
        """
        Run low-priority periodic jobs only when SEARCHING.

        Priority order:
        1) Skill refresh (timer-based)
        2) Auto-equip
        3) Quest cycle

        At most one heavy task runs per loop iteration to keep control loop responsive.
        """
        if self.state != BotState.SEARCHING or self.is_performing_action:
            return

        if self._run_skill_refresh_task(img_bgr, current_time):
            return

        if self._run_auto_equip_task(img_bgr, game_region):
            return

        self._run_quest_task(img_bgr, game_region, current_time)

    def _run_skill_refresh_task(self, img_bgr: np.ndarray, current_time: float) -> bool:
        if not self.auto_skill_enabled:
            return False

        if current_time - self.last_skill_check < self.skill_check_interval:
            return False

        self.last_skill_check = current_time
        if not (hasattr(self, "skill_manager") and self.skill_manager and self.skill_manager.can_refresh()):
            return True

        self.is_performing_action = True
        try:
            self.skill_manager.check_and_refresh(
                img_bgr,
                log_callback=self.log,
                context="SKILL",
                fast_cast=True,
            )
        finally:
            self.is_performing_action = False

        return True

    def _run_auto_equip_task(self, img_bgr: np.ndarray, game_region: Dict[str, int]) -> bool:
        if not self.auto_equip_enabled or not self.inventory_manager.can_check():
            return False

        self.is_performing_action = True
        try:
            self.inventory_manager.capturer = self.capturer  # type: ignore[assignment]
            self.inventory_manager.driver = self.driver  # type: ignore[assignment]

            def get_fresh_screenshot(game_region_snapshot=game_region):
                return self.get_game_screenshot(game_region_snapshot)

            action_taken = self.inventory_manager.check_and_equip(
                frame=img_bgr,
                game_region=game_region,
                log_callback=self.log,
                capture_callback=get_fresh_screenshot,
            )

            if action_taken:
                self.log("[AUTO-EQUIP] Ekipman kontrolu tamamlandi.")
        except Exception as equip_error:
            self.log(f"[AUTO-EQUIP] [ERROR] {equip_error}")
        finally:
            self.is_performing_action = False

        return True

    def _run_quest_task(self, img_bgr: np.ndarray, game_region: Dict[str, int], current_time: float) -> bool:
        if not self.quest_enabled:
            return False

        if current_time - self.last_quest_check < self.quest_check_interval:
            return False

        try:
            if self.quest_handler.should_run(img_bgr, game_region, self.log):
                self.log("[QUEST] Trigger condition met. Starting renewal cycle...")
                self.is_performing_action = True
                try:
                    self.quest_handler.capturer = self.capturer
                    self.quest_handler.driver = self.driver  # type: ignore[assignment]

                    def get_quest_screenshot(game_region_snapshot=game_region):
                        return self.get_game_screenshot(game_region_snapshot)

                    success = self.quest_handler.perform_cycle(
                        frame=img_bgr,
                        game_region=game_region,
                        log_callback=self.log,
                        capture_callback=get_quest_screenshot,
                    )

                    if success:
                        self.log("[QUEST] Cycle finished successfully.")
                    else:
                        self.log("[QUEST] Cycle failed.")
                finally:
                    self.is_performing_action = False
        except Exception as quest_error:
            self.log(f"[QUEST] [ERROR] {quest_error}")
            self.is_performing_action = False
        finally:
            self.last_quest_check = current_time

        return True

    async def solve_captcha_sequence(self, captcha_rect: Dict[str, int], game_region: dict, screenshot: np.ndarray) -> bool:  # NOSONAR
        """
        Execute CAPTCHA solving with 2x3 grid mapping and anti-ban retries.
        
        Args:
            captcha_rect: Bounding box of CAPTCHA pop-up header
            game_region: Game window region information
            screenshot: Full screenshot image (BGR format)
        
        Returns:
            True if CAPTCHA solved, False if max attempts reached or bot stopped
        """
        _ = screenshot

        captcha_cfg = self.global_captcha_cfg if isinstance(self.global_captcha_cfg, dict) else {}
        api_key = str(captcha_cfg.get("api_key", "")).strip() or None
        model_name = str(captcha_cfg.get("selected_model", DEFAULT_GEMINI_MODEL)).strip() or DEFAULT_GEMINI_MODEL

        attempts = 0
        MAX_ATTEMPTS = 3
        current_rect = dict(captcha_rect)
        
        while attempts < MAX_ATTEMPTS:
            attempts += 1

            if self.stop_event and self.stop_event.is_set():
                return False
            
            self.log(f"\n{'='*70}")
            self.log(f"[CAPTCHA] DENEME {attempts}/{MAX_ATTEMPTS}")
            self.log(f"{'='*70}")
            
            fresh_screenshot = self.get_game_screenshot(game_region)
            if fresh_screenshot is None:
                self.log("[HATA] Ekran goruntusu alinamadi")
                self.smart_sleep(0.2)
                continue
            
            found, fresh_rect = Vision.check_for_captcha(fresh_screenshot)
            
            if not found:
                self.log("[OK] Captcha Cozuldu/Gitti.")
                self.log(f"{'='*70}\n")
                return True

            if isinstance(fresh_rect, dict):
                current_rect = fresh_rect
            
            self.log("[CAPTCHA] Tespit edildi, cozuluyor...")

            if self.driver is None:
                self.log("[HATA] Driver hazir degil, CAPTCHA cozum islemi iptal")
                return False
            
            try:
                self.log("[BILGI] CAPTCHA grid resmi cikariliyor...")
                
                extraction_result = Vision.get_captcha_grid_image(current_rect, fresh_screenshot, temp_dir="temp")
                
                if extraction_result is None:
                    self.log("[HATA] CAPTCHA grid cikarilamadi")
                    self.smart_sleep(0.2)
                    continue
                
                grid_image_path, grid_origin = extraction_result
                self.log(f"[OK] Grid cikarildi: {grid_image_path}")
                
                self.log(f"[BILGI] Gemini API'ye gonderiliyor... (model: {model_name})")
                predicted_square = await asyncio.wait_for(
                    solve_captcha_with_gemini(
                        grid_image_path,
                        api_key=api_key,
                        model_name=model_name,
                    ),
                    timeout=8.5,
                )
                
                if predicted_square is None:
                    self.log("[HATA] Gemini cozemedi")
                    self.smart_sleep(0.2)
                    continue
                
                predicted_square = int(predicted_square)
                if predicted_square < 1 or predicted_square > 6:
                    self.log(f"[HATA] Gecersiz captcha index: {predicted_square}")
                    self.smart_sleep(0.2)
                    continue

                self.log(f"[OK] Gemini tahmini: Kare {predicted_square}")
                
                local_click = self._captcha_pick_local_click_point(
                    captcha_rect=current_rect,
                    image_index=predicted_square,
                    frame_shape=fresh_screenshot.shape,
                    grid_origin=grid_origin,
                )

                if local_click is None:
                    self.log("[HATA] Captcha click koordinati hesaplanamadi")
                    self.smart_sleep(0.2)
                    continue

                local_x, local_y = local_click
                if self.capturer and self.capturer.target_hwnd:
                    click_x, click_y = self.capturer.get_screen_position(local_x, local_y)
                else:
                    click_x = int(game_region['left'] + local_x)
                    click_y = int(game_region['top'] + local_y)

                self.log(f"[TIKLA] Kare {predicted_square} tiklaniyor ({click_x}, {click_y})")

                clicked = self.human_click(click_x, click_y, fast=True)
                if not clicked:
                    self.log("[HATA] Captcha tiklamasi basarisiz")
                    self.smart_sleep(0.2)
                    continue

                self.log("[CAPTCHA] Hizli dogrulama baslatildi...")
                captcha_resolved = False
                button_clicked = False
                screenshot_after = None
                verify_deadline = time.time() + 1.25

                while time.time() < verify_deadline:
                    screenshot_after = self.get_game_screenshot(game_region)
                    if screenshot_after is None:
                        self.smart_sleep(0.06)
                        continue

                    if not button_clicked:
                        found_button, button_rect = Vision.find_onayla_button(screenshot_after, threshold=0.62)
                        if found_button and button_rect:
                            button_local_x = int(button_rect['center_x'])
                            button_local_y = int(button_rect['center_y'])
                            if self.capturer and self.capturer.target_hwnd:
                                button_x, button_y = self.capturer.get_screen_position(button_local_x, button_local_y)
                            else:
                                button_x = int(game_region['left'] + button_local_x)
                                button_y = int(game_region['top'] + button_local_y)

                            self.log(f"[OK] Onayla butonu bulundu ({button_x}, {button_y})")
                            self.human_click(button_x, button_y, fast=True)
                            button_clicked = True
                            self.smart_sleep(0.08)
                            continue

                    still_visible, _ = Vision.check_for_captcha(screenshot_after, threshold=0.66)
                    if not still_visible:
                        captcha_resolved = True
                        break

                    self.smart_sleep(0.07)

                if captcha_resolved:
                    self.log("[OK] Captcha basariyla cozuldu.")
                    self.log(f"{'='*70}\n")
                    return True

                if screenshot_after is None:
                    self.log("[UYARI] CAPTCHA dogrulama goruntusu alinamadi")
                    continue

                self.log(f"[BILGI] Deneme {attempts} sonrasi CAPTCHA hala gorunuyor.")
            except asyncio.TimeoutError:
                self.log("[HATA] Gemini yaniti zaman asimina ugradi (8.5s). Tekrar denenecek.")
                self.smart_sleep(0.1)
                continue
                
            except Exception as e:
                self.log(f"[HATA] Deneme sirasinda hata: {e}")
                import traceback
                traceback.print_exc()
                self.smart_sleep(0.2)
                continue
            
            finally:
                try:
                    for temp_name in (
                        "combined_captcha_grid.png",
                        "gemini_debug_view.png",
                    ):
                        temp_file = os.path.join("temp", temp_name)
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                except Exception:
                    pass
        
        self.log(f"\n{'='*70}")
        self.log("[HATA] Captcha 3 denemede cozulmedi. Guvenlik icin bot durduruluyor.")
        self.log(f"{'='*70}\n")
        if self.stop_event:
            self.stop_event.set()
        return False

    def run_bot_logic(self, mouse_id: int, selected_map_name: str, loot_enabled: bool,  # NOSONAR
                      combat_timeout: int, bot_mode: str, buff_key_1: str, buff_key_2: str, 
                      revive_wait_time: int, auto_skill_enabled: bool, skill_key_1: str,
                      skill_key_2: str, skill_interval: int,
                      log_callback: Callable, stop_event: threading.Event,
                      stats_callback: Optional[Callable] = None,
                      overlay_callback: Optional[Callable] = None,
                      status_callback: Optional[Callable[[str], None]] = None,
                      target_hwnd: Optional[int] = None,
                      skill_settings: Optional[dict] = None,
                      miss_timeout: float = 1.5,
                      movement_timeout: float = 8.0,
                      verify_timeout: float = 3.0,
                      strafe_start_delay: float = 2.0,
                      strafe_interval: float = 1.0,
                      multi_target_queue_enabled: bool = False,
                      multi_target_queue_size: int = 3,
                      quest_enabled: bool = False,
                      quest_check_interval: float = 3.0):
        """
        Main bot logic with Visual State Machine.
        
        Args:
            target_hwnd: Direct window handle from ProcessManager.
                        If provided, skips title-based window search.
        """
        _ = bot_mode, skill_interval
        # Run the async main loop in a new event loop
        asyncio.run(self._async_run_bot_logic(mouse_id, selected_map_name, loot_enabled, combat_timeout, 
                                              bot_mode, buff_key_1, buff_key_2, revive_wait_time,
                                              auto_skill_enabled, skill_key_1, skill_key_2, skill_interval,
                                              log_callback, stop_event, stats_callback, overlay_callback,
                                              status_callback,
                                              target_hwnd, skill_settings, miss_timeout, movement_timeout,
                                              verify_timeout, strafe_start_delay, strafe_interval,
                                              multi_target_queue_enabled, multi_target_queue_size,
                                              quest_enabled, quest_check_interval))

    async def _async_run_bot_logic(self, mouse_id: int, selected_map_name: str, loot_enabled: bool,  # NOSONAR
                                   combat_timeout: int, bot_mode: str, buff_key_1: str, buff_key_2: str, 
                                   revive_wait_time: int, auto_skill_enabled: bool, skill_key_1: str,
                                   skill_key_2: str, skill_interval: int,
                                   log_callback: Callable, stop_event: threading.Event,
                                   stats_callback: Optional[Callable] = None,
                                   overlay_callback: Optional[Callable] = None,
                                   status_callback: Optional[Callable[[str], None]] = None,
                                   target_hwnd: Optional[int] = None,
                                   skill_settings: Optional[dict] = None,
                                   miss_timeout: float = 1.5,
                                   movement_timeout: float = 8.0,
                                   verify_timeout: float = 3.0,
                                   strafe_start_delay: float = 2.0,
                                   strafe_interval: float = 1.0,
                                   multi_target_queue_enabled: bool = False,
                                   multi_target_queue_size: int = 3,
                                   quest_enabled: bool = False,
                                   quest_check_interval: float = 3.0):
        _ = bot_mode
        self.stop_event = stop_event
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.overlay_callback = overlay_callback
        self.status_callback = status_callback
        self.combat_timeout = combat_timeout  # Store timeout for use in combat state
        self.buff_key_1 = buff_key_1
        self.buff_key_2 = buff_key_2
        self.revive_wait_time = revive_wait_time
        self.loot_enabled = loot_enabled  # Store for conditional Z key presses
        self.current_mouse_id = int(mouse_id)
        self.no_hp_click_failures = 0
        self.target_hwnd = target_hwnd  # Store HWND from ProcessManager
        self.miss_timeout = max(1.0, float(miss_timeout))
        self.movement_timeout = max(3.0, float(movement_timeout))
        self.verify_timeout = max(1.0, float(verify_timeout))
        self.strafe_start_delay = max(1.0, float(strafe_start_delay))
        self.strafe_interval = max(1.0, float(strafe_interval))
        self.multi_target_queue_enabled = bool(multi_target_queue_enabled)
        try:
            parsed_queue_size = int(multi_target_queue_size)
        except Exception:
            parsed_queue_size = 3
        self.multi_target_queue_size = max(1, parsed_queue_size)
        self.queued_targets_count = 0
        self.queue_wait_until = 0.0
        
        # Istatistik sayaclari
        self.stones_destroyed = 0
        self.bot_start_time = time.time()
        
        # Skill sistemi ayarlari (REACTIVE)
        self.auto_skill_enabled = auto_skill_enabled
        self.skill_key_1 = skill_key_1
        self.skill_key_2 = skill_key_2
        self.skill_check_interval = max(5.0, float(skill_interval))
        self.last_skill_check = 0  # Reactive buff check timer
        self.skill_settings = skill_settings if isinstance(skill_settings, dict) else {}
        self.quest_enabled = bool(quest_enabled)
        self.quest_check_interval = max(1.0, float(quest_check_interval))
        self._refresh_global_captcha_config()
        self.config.setdefault("general", {})["captcha_solver"] = bool(self.global_captcha_cfg.get("enabled", True))

        if not self.skill_settings:
            self.skill_settings = {
                "active_profile": "savasci_bedensel",
                "skill_1": {"key": self.skill_key_1, "cooldown": 60, "enabled": True},
                "skill_2": {"key": self.skill_key_2, "cooldown": 180, "enabled": True},
                "skill_3": {"key": "3", "cooldown": 20, "enabled": False},
                "buff_1": {"key": buff_key_1, "cooldown": 200, "enabled": False},
                "buff_2": {"key": buff_key_2, "cooldown": 200, "enabled": False},
            }
        
        try:
            self.log(f"[BASLAT] Motor Basladi: {selected_map_name} (Mouse ID: {mouse_id})")

            try:
                if hasattr(self, "vision") and self.vision and hasattr(self.vision, "get_runtime_info"):
                    info = self.vision.get_runtime_info()
                    self.log(
                        f"[YOLO] Runtime: device={info.get('device')} half={info.get('half')} "
                        f"fps={info.get('max_infer_fps')} imgsz={info.get('input_size')}"
                    )
            except Exception:
                pass
            
            if self.quest_enabled:
                self.log(f"[QUEST] System ENABLED (Check: {self.quest_check_interval:.0f}s)")

            try:
                if hasattr(self, "skill_manager") and self.skill_manager:
                    self.skill_manager.configure(self.skill_settings, log_callback=self.log)
                    # Mount detection is unreliable; we start each run assuming mounted.
                    self.skill_manager.is_mounted = True
                    self.log("[SKILL] Mount state reset: assumed mounted at bot start.")
            except Exception as skill_cfg_error:
                self.log(f"[WARN] Skill profile configure failed: {skill_cfg_error}")

            if not self.vision:
                self.log("[HATA] AI Vision baslatilamadi.")
                return
            
            # Initialize driver
            try:
                self.driver = DriverBot(mouse_id=mouse_id, log_callback=self.log)
                self.process_manager.set_input_flush_callback(self.driver.release_all_inputs)
                self.log("[OK] Driver baslatildi")
            except Exception as e:
                self.log(f"[HATA] Driver baslatilamadi: {e}")
                return
            
            # Initialize WindowCapture (MSS - Stable Cross-Platform)
            try:
                TARGET_TITLES = ["Rubinum", "Saryong", "Metin2", "Client", "Game"]
                
                # Use HWND from ProcessManager if provided (NEW)
                if self.target_hwnd:
                    self.log(f"[OK] Using attached window (HWND: {self.target_hwnd})")
                    self.capturer = WindowCapture(target_titles=TARGET_TITLES, target_hwnd=self.target_hwnd)
                else:
                    # Fallback to title-based search
                    self.log("[INFO] No HWND provided, searching by title...")
                    self.capturer = WindowCapture(target_titles=TARGET_TITLES)
                
                # Check if initialization was successful
                if not self.capturer.target_hwnd:
                    self.log("[HATA] Oyun penceresi bulunamadi.")
                    self.log(f"[INFO] Aranan pencere basliklari: {TARGET_TITLES}")
                    return
                
                if not self.capturer.sct:
                    self.log("[HATA] MSS baslatılamadi.")
                    return
                
                self.log("[OK] WindowCapture baslatildi (MSS Backend)")
                
            except Exception as e:
                self.log(f"[HATA] WindowCapture baslatilamadi: {e}")
                import traceback
                traceback.print_exc()
                return
            
            # OBJECTIVE 1: Auto-focus game window and countdown
            self.log("[BASLAT] Oyun penceresine geciliyor...")
            if self.target_hwnd:
                try:
                    from core.utils import window_manager as wm
                    wm.bring_window_to_front_by_hwnd(self.target_hwnd)
                except Exception as focus_error:
                    self.log(f"[UYARI] Pencere odaklama basarisiz: {focus_error}")
            else:
                self.log("[UYARI] Seçili HWND yok, odaklama atlandi")

            
            # 3-second countdown
            self.log("3 saniye icinde basliyor...")
            self.smart_sleep(1)
            self.log("2 saniye icinde basliyor...")
            self.smart_sleep(1)
            self.log("1 saniye icinde basliyor...")
            self.smart_sleep(1)
            self.log("[OK] BOT BASLADI!")
            
            # REACTIVE: Initial skill check will happen in first loop iteration
            # No unconditional skill casting at startup - let the visual detection decide
            if self.auto_skill_enabled:
                self.log(f"[SKILL] Reaktif skill sistemi aktif. Bufflar {self.skill_check_interval:.0f} saniyede bir kontrol edilecek.")
                self.last_skill_check = 0  # Force immediate check on first loop
            
            # CRITICAL: Explicitly set state to SEARCHING
            self.log("[OK] Ana dongu aktif. Acil durdurma icin F12'ye basin.")
            self.state = BotState.SEARCHING
            self.is_on_break = False
            self.break_started_at = 0.0
            self.break_duration = 0.0
            self._last_break_status_emit_at = 0.0
            self._last_break_minutes_left = -1
            self._reset_session_timer()
            self._emit_session_status("Status: Running...", force=True)
            
            self.last_state = None  # For state change logging
            self.last_global_loot_time = time.time()  # Global loot zamanlayici
            self.last_heartbeat_time = time.time()  # For search heartbeat logs
            self.loop_iteration = 0  # Loop heartbeat counter
            
            # ============================================================
            # VISUAL SNAPSHOT DEBUGGER - Setup (Only in DEBUG_MODE)
            # ============================================================
            if DEBUG_MODE:
                debug_dir = "temp_debug"
                try:
                    os.makedirs(debug_dir, exist_ok=True)
                    for old_file in os.listdir(debug_dir):
                        if old_file.startswith("search_view_") and old_file.endswith(".jpg"):
                            try:
                                os.remove(os.path.join(debug_dir, old_file))
                            except Exception:
                                pass
                except Exception as e:
                    pass
            
            # Main loop
            while not self.stop_event.is_set():
                # ============================================================
                # LOOP HEARTBEAT - Confirm loop is running
                # ============================================================
                self.loop_iteration += 1
                current_time = time.time()
                
                # First iteration - confirm loop started
                if self.loop_iteration == 1:
                    self.log("[OK] Ana dongu basarili! Tas araniyor...")
                    self._last_alive_log = current_time
                
                # Log every 5 seconds that we're alive
                if not hasattr(self, '_last_alive_log'):
                    self._last_alive_log = current_time
                if current_time - self._last_alive_log >= 5.0:
                    self.log(f"[ALIVE] Loop iteration: {self.loop_iteration} | State: {self.state.value}")
                    self._last_alive_log = current_time
                
                # Failsafe check
                if keyboard.is_pressed('F12'):
                    self.log("[ACIL] Acil durdurma tetiklendi (F12)")
                    break

                break_idle, break_sleep = self._update_session_manager(current_time)
                if break_idle:
                    self._emit_overlay([])
                    await asyncio.sleep(max(0.05, break_sleep))
                    continue
                
                try:
                    # ============================================================
                    # ISSUE 2 FIX: Focus Safety Gate (Main Loop)
                    # Skip ALL input actions if game window is not focused
                    # ============================================================
                    if not self.is_game_focused():
                        # Log only once per second to avoid spam
                        if not hasattr(self, '_last_focus_log') or current_time - self._last_focus_log >= 1.0:
                            self.log("[WAIT] Game window lost focus. Pausing all actions...")
                            self._last_focus_log = current_time
                        self._emit_overlay([])
                        await asyncio.sleep(0.5)  # Prevent CPU spinning
                        continue  # Skip this iteration entirely
                    
                    # ============================================================
                    # GLOBAL LOOT - Her 2 saniyede 3 kez Z bas (only if enabled)
                    # ============================================================
                    if self.loot_enabled and (current_time - self.last_global_loot_time >= 2.0):
                        pydirectinput.press('z', presses=3, interval=0.05)
                        self.last_global_loot_time = current_time
                    

                    # Find game window
                    if self.loop_iteration <= 3:
                        self.log(f"[DIAG:{self.loop_iteration}] Pencere araniyor...")
                    game_region = self.find_game_window()
                    if not game_region:
                        self.log("[UYARI] Oyun penceresi bulunamadi. Bekleniyor...")
                        self.smart_sleep(2)
                        continue
                    
                    # Capture screenshot
                    if self.loop_iteration <= 3:
                        self.log(f"[DIAG:{self.loop_iteration}] Ekran goruntusu aliniyor...")
                    img_bgr = self.get_game_screenshot(game_region)
                    if img_bgr is None:
                        # Only log every 50 iterations to avoid spam
                        if self.loop_iteration <= 3 or self.loop_iteration % 50 == 0:
                            self.log(f"[HATA] Ekran goruntusu alinamadi! (iter: {self.loop_iteration})")
                        self.smart_sleep(0.1)
                        continue
                    
                    if self.loop_iteration <= 3:
                        self.log(f"[DIAG:{self.loop_iteration}] Olum kontrolu yapiliyor...")
                    
                    # ============================================================
                    # AUTO-REVIVE CHECK (Priority Check)
                    # ============================================================
                    # First check for pending death from Combat loop (instant detection)
                    if hasattr(self, '_pending_death') and self._pending_death:
                        game_region_death, dead_pos_local = self._pending_death
                        self._pending_death = None  # Clear flag
                        self.log("[HIZLI] Combat'tan gelen olum sinyali isleniyor...")
                        self._handle_death_protocol(game_region_death, dead_pos_local, self.revive_wait_time)
                        self.queued_targets_count = 0
                        self.queue_wait_until = 0.0
                        self.state = BotState.SEARCHING
                        continue
                    
                    # Normal death check (for non-combat states)
                    is_dead, dead_pos_local = Vision.check_if_dead(img_bgr, game_region)
                    if is_dead and dead_pos_local:
                        self._handle_death_protocol(game_region, dead_pos_local, self.revive_wait_time)
                        self.queued_targets_count = 0
                        self.queue_wait_until = 0.0
                        self.state = BotState.SEARCHING # Reset state
                        continue # Skip rest of loop
                    
                    # ============================================================
                    # CRITICAL: CHECK FOR CAPTCHA BEFORE ANY OTHER BOT ACTION
                    # ============================================================
                    captcha_detected, captcha_rect = Vision.check_for_captcha(img_bgr)
                    global_captcha_enabled = bool(self.global_captcha_cfg.get("enabled", True))
                    
                    if captcha_detected and global_captcha_enabled:
                        if not self.captcha_active:
                            self.captcha_active = True
                            self.log("[CAPTCHA] Tespit edildi! Bot duraklatiliyor.")
                        
                        # Run async CAPTCHA solving sequence with error handling
                        try:
                            if captcha_rect is None:
                                self.log("[CAPTCHA] Tespit var ancak koordinat yok, tekrar denenecek")
                                self.smart_sleep(0.2)
                                continue
                            await self.solve_captcha_sequence(captcha_rect, game_region, img_bgr)
                        except ValueError as ve:
                            # Critical error - API key missing or invalid
                            self.log(f"[KRITIK HATA] {str(ve)}")
                            self.log("[KRITIK HATA] Sonsuz donguyu onlemek icin bot durduruluyor.")
                            stop_event.set()
                            break
                        except Exception as e:
                            # Other errors during CAPTCHA solving
                            self.log(f"[KRITIK HATA] {str(e)}")
                            self.log("[KRITIK HATA] Sonsuz donguyu onlemek icin bot durduruluyor.")
                            stop_event.set()
                            break
                        
                        self.smart_sleep(0.5)
                        continue

                    if captcha_detected and not global_captcha_enabled:
                        self.log("[CAPTCHA] Tespit edildi fakat global CAPTCHA solver kapali.")
                        if stop_event:
                            stop_event.set()
                        break
                    
                    # If CAPTCHA was active but is now gone, resume bot activity
                    if self.captcha_active:
                        self.captcha_active = False
                        self.log("[OK] CAPTCHA Cozuldu! Bot devam ediyor.")
                        # Clear any lingering inputs
                        pydirectinput.keyUp('w')
                        pydirectinput.keyUp('a')
                        pydirectinput.keyUp('s')
                        pydirectinput.keyUp('d')
                        pydirectinput.keyUp('q')
                        pydirectinput.keyUp('z')
                        self.smart_sleep(0.5)
                    
                    # ============================================================
                    # SKIP ALL BOT LOGIC IF CAPTCHA IS ACTIVE
                    # ============================================================
                    if self.captcha_active:
                        self.smart_sleep(0.5)
                        continue
                    
                    # ============================================================
                    # GLOBAL VISION UPDATE
                    # High-cost YOLO inference runs only in SEARCHING state.
                    # MOVING/VERIFY rely on fast HP checks and should not be delayed.
                    # ============================================================
                    all_detections = self.vision.get_recent_detections(max_age_ms=220.0)
                    target_positions = []
                    overlay_detections = []

                    if self.state == BotState.SEARCHING:
                        if self.loop_iteration <= 3:
                            self.log(f"[DIAG:{self.loop_iteration}] YOLO inference basliyor...")

                        try:
                            queue_limit = self.multi_target_queue_size if self.multi_target_queue_enabled else 1
                            local_target_positions, all_detections = self.vision.get_top_targets(
                                img=img_bgr,
                                max_targets=max(1, int(queue_limit)),
                                mask_regions=self._get_mask_regions(),
                            )

                            if self.loop_iteration <= 3:
                                self.log(f"[DIAG:{self.loop_iteration}] YOLO tamamlandi. Tespit: {len(all_detections)}")

                            if DEBUG_MODE and self.loop_iteration % 40 == 0:
                                self.log(f"[VISION] YOLO detected {len(all_detections)} objects in this frame.")

                        except Exception as vision_error:
                            self.log(f"[ERROR] Vision detection failed: {vision_error}")
                            import traceback
                            traceback.print_exc()
                            all_detections = self.vision.get_recent_detections(max_age_ms=220.0)
                            local_target_positions = []

                        if local_target_positions and self.capturer:
                            converted_targets = []
                            for local_pos in local_target_positions:
                                local_x, local_y = int(local_pos[0]), int(local_pos[1])
                                global_x, global_y = self.capturer.get_screen_position(local_x, local_y)
                                converted_targets.append((global_x, global_y))
                            target_positions = converted_targets

                    if self.capturer:
                        for det in all_detections:
                            try:
                                local_rect = det.get('rect', [])
                                if len(local_rect) == 4:
                                    overlay_detections.append({
                                        'rect': [
                                            float(local_rect[0]), float(local_rect[1]),
                                            float(local_rect[2]), float(local_rect[3])
                                        ],
                                        'label': det.get('label', 'metin'),
                                        'conf': det.get('conf', 0.0)
                                    })
                            except Exception:
                                pass

                    # Keep overlay updates centralized and consistent across loop ticks.
                    self._emit_overlay(overlay_detections)
                    
                    # ============================================================
                    # GOD MODE: VISUAL DEBUGGING (Only in DEBUG_MODE)
                    # ============================================================
                    if DEBUG_MODE:
                        try:
                            debug_frame = img_bgr.copy()
                            
                            # Draw bounding boxes for all detections
                            for detection in all_detections:
                                rect = detection.get('rect', [])
                                if len(rect) != 4:
                                    continue
                                x1, y1, x2, y2 = rect
                                confidence = float(detection.get('conf', 0.0))
                                
                                # Draw rectangle (Green for high confidence, Yellow for low)
                                color = (0, 255, 0) if confidence > 0.6 else (0, 255, 255)
                                cv2.rectangle(debug_frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                                
                                # Draw confidence text
                                label = f"{confidence:.2f}"
                                cv2.putText(debug_frame, label, (int(x1), int(y1) - 10),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                            
                            # Draw state and detection count
                            state_text = f"State: {self.state.value} | Detections: {len(all_detections)}"
                            cv2.putText(debug_frame, state_text, (10, 30),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                            
                            # Show debug window
                            cv2.imshow("DEBUG VIEW - Bot Vision", debug_frame)
                            cv2.waitKey(1)
                            
                            # Save snapshots periodically - DISABLED
                            # if self.loop_iteration % 50 == 0 and self.state == BotState.SEARCHING:
                            #     try:
                            #         timestamp = int(time.time())
                            #         filename = f"temp_debug/search_view_{timestamp}_iter{self.loop_iteration}.jpg"
                            #         cv2.imwrite(filename, debug_frame)
                            #     except:
                            #         pass
                                
                        except Exception:
                            pass

                    # ============================================================
                    # STATE TRANSITION LOGGING (Only log when state changes)
                    # ============================================================
                    if self.state != self.last_state:
                        self.log(f"[STATE] Transitioned to: {self.state.value}")
                        self.last_state = self.state
                    
                    # ============================================================
                    # SEARCH STATE HEARTBEAT (Show we're alive)
                    # ============================================================
                    if self.state == BotState.SEARCHING:
                        if current_time - self.last_heartbeat_time >= 2.0:
                            self.log(f"[ARAMA] Taslar: {len(all_detections)} | Hedef: {'BULUNDU' if target_positions else 'YOK'}")
                            self.last_heartbeat_time = current_time
                    
                    # STATE MACHINE - ONLY RUN IF CAPTCHA NOT ACTIVE
                    next_state = None
                    
                    if self.state == BotState.SEARCHING:
                        # Pass already converted global targets to SEARCH state.
                        next_state = self.state_searching(img_bgr, game_region, target_positions, overlay_detections)
                    
                    elif self.state == BotState.MOVING_TO_TARGET:
                        # Anti-stuck: random strafing during movement
                        next_state = self.state_moving_to_target(img_bgr, game_region)
                    
                    elif self.state == BotState.VERIFY_ATTACK:
                        next_state = self.state_verify_attack(img_bgr, game_region)
                    
                    elif self.state == BotState.COMBAT:
                        next_state = self.state_combat(img_bgr, game_region)

                    elif self.state == BotState.QUEUE_WAIT:
                        next_state = self.state_queue_wait(img_bgr, game_region)
                    
                    elif self.state == BotState.LOOT:
                        if loot_enabled:
                            next_state = self.state_loot()
                        else:
                            self.log("[BILGI] Loot devre disi. ARAMA'ya geciliyor.")
                            next_state = BotState.SEARCHING
                    
                    # Transition to next state
                    if next_state is not None:
                        self.log(f"[STATE CHANGE] {self.state.value} -> {next_state.value}")
                        
                        # ZERO-LATENCY: Removed sleep after combat for instant target switching
                        # Previously: if self.state == BotState.COMBAT and next_state == BotState.SEARCHING:
                        #                 self.smart_sleep(0.5)
                        
                        self.state = next_state
                        self.last_state = None  # Force log on next iteration

                    # Low-priority jobs are intentionally deferred until core state logic completes.
                    self._run_non_critical_tasks(img_bgr, game_region, time.time())
                    
                except KeyboardInterrupt:
                    self.log("[ACIL] Kullanici tarafindan kesildi")
                    break
                except Exception as e:
                    self.log(f"[HATA] Dongu hatasi: {e}")
                    self.smart_sleep(1)
            
            self.log("[BILGI] Motor durduruldu.")
            self._emit_overlay([])
            
        except Exception as e:
            self.log(f"[KRITIK HATA] Olumcul hata: {e}")
            self._emit_overlay([])
            import traceback
            traceback.print_exc()
        finally:
            try:
                self.process_manager.set_input_flush_callback(None)
            except Exception:
                pass

            if self.driver:
                try:
                    self.driver.release_all_inputs()
                except Exception:
                    pass

                try:
                    self.driver.close()
                except Exception:
                    pass


# Module-level function for threading
def run_bot_logic(mouse_id: int, selected_map_name: str, loot_enabled: bool,  # NOSONAR
                  log_callback: Callable, stop_event: threading.Event, combat_timeout: int = 60, 
                  bot_mode: str = "METIN_FARM", buff_key_1: str = "4", buff_key_2: str = "f2",
                  revive_wait_time: int = 15, auto_skill_enabled: bool = True,
                  skill_key_1: str = "4", skill_key_2: str = "f2", skill_interval: int = 65,
                  stats_callback: Optional[Callable] = None,
                  status_callback: Optional[Callable[[str], None]] = None,
                  skill_settings: Optional[dict] = None,
                  miss_timeout: float = 1.5,
                  movement_timeout: float = 8.0,
                  verify_timeout: float = 3.0,
                  strafe_start_delay: float = 2.0,
                  strafe_interval: float = 1.0,
                  multi_target_queue_enabled: bool = False,
                  multi_target_queue_size: int = 3,
                  quest_enabled: bool = False,
                  quest_check_interval: float = 3.0):
    """Entry point for bot thread"""
    engine = BotEngine()
    engine.run_bot_logic(mouse_id, selected_map_name, loot_enabled, combat_timeout, bot_mode, 
                         buff_key_1, buff_key_2, revive_wait_time, auto_skill_enabled,
                         skill_key_1, skill_key_2, skill_interval, log_callback, stop_event,
                         stats_callback, None, status_callback, None, skill_settings,
                         miss_timeout, movement_timeout,
                         verify_timeout, strafe_start_delay, strafe_interval,
                         multi_target_queue_enabled, multi_target_queue_size,
                         quest_enabled, quest_check_interval)
