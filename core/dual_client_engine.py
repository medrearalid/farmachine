"""
Dual-client orchestration engine for overlapped Metin2 windows.

Key properties:
- Event-driven zero-idle task scheduler
- Strict context switch focus gate via ProcessManager.switch_context()
- Fully visual skill refresh (no cooldown gating)
- Per-client isolated runtime state via BotContext dataclass
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import keyboard
import numpy as np
import pydirectinput

from core.ai.gemini_client import DEFAULT_GEMINI_MODEL, solve_captcha_with_gemini
from core.drivers.driver_bot import DriverBot
from core.dpi_utils import calculate_relative_rect
from core.inventory_manager import InventoryManager
from core.mount_checker import MountChecker
from core.process_manager import get_process_manager
from core.quest_handler import QuestHandler
from core.skill_manager import SkillManager
from core.vision.vision import Vision
from core.vision_ai import YoloVision, point_in_mask_regions
from core.window_capture import WindowCapture


class BotState(Enum):
    SEARCHING = "searching"
    BATCH_QUEUEING = "batch_queueing"
    EXECUTING_QUEUE = "executing_queue"
    SOLVING_CAPTCHA = "solving_captcha"
    MOVING_TO_TARGET = "moving_to_target"
    VERIFY_ATTACK = "verify_attack"
    COMBAT = "combat"
    QUEUE_WAIT = "queue_wait"
    LOOT = "loot"


@dataclass
class BotContext:
    slot: str
    hwnd: int
    config: Dict[str, Any]
    active_profile: str
    state: BotState = BotState.SEARCHING
    state_started_at: float = field(default_factory=time.time)
    movement_started_at: float = 0.0
    combat_started_at: float = 0.0
    last_strafe_at: float = 0.0
    combat_last_z_at: float = 0.0
    combat_hp_missing_count: int = 0
    combat_strafe_direction: str = "a"
    last_skill_check: float = 0.0
    last_quest_check: float = 0.0
    last_heartbeat_at: float = field(default_factory=time.time)
    last_roam_at: float = 0.0
    action_locked_until: float = 0.0
    captcha_active: bool = False
    captcha_wait_until: float = 0.0
    captcha_attempts: int = 0
    captcha_request_token: int = 0
    captcha_solver_inflight: bool = False
    captcha_solution_index: Optional[int] = None
    captcha_rect_cache: Optional[Dict[str, int]] = None
    captcha_grid_origin: Optional[Tuple[int, int]] = None
    captcha_verify_deadline: float = 0.0
    captcha_button_clicked: bool = False
    captcha_cooldown_until: float = 0.0
    no_hp_click_failures: int = 0
    last_recalibration_attempt: float = 0.0
    failed_click_count: int = 0
    last_clicked_pos: Optional[Tuple[int, int]] = None
    stones_destroyed: int = 0
    bot_started_at: float = field(default_factory=time.time)
    last_profile_log_at: float = 0.0
    last_kill_at: float = 0.0
    post_kill_priority_until: float = 0.0
    post_kill_reacquire_pending: bool = False
    queued_targets_count: int = 0
    queue_wait_until: float = 0.0
    deferred_queue_remaining: int = 0
    deferred_queue_click_at: float = 0.0
    multi_click_lock_active: bool = False
    queued_stone_ids: Set[Tuple[int, int]] = field(default_factory=set)
    hp_acquire_deadline: float = 0.0
    session_start_time: float = field(default_factory=time.time)
    next_break_threshold: float = 0.0
    is_on_break: bool = False
    break_started_at: float = 0.0
    break_duration: float = 0.0
    last_break_status_at: float = 0.0
    last_break_minutes_left: int = -1

    # Isolated managers/state holders (per-client)
    capturer: Optional[WindowCapture] = None
    vision: Optional[YoloVision] = None
    skill_manager: Optional[SkillManager] = None
    inventory_manager: Optional[InventoryManager] = None
    quest_handler: Optional[QuestHandler] = None
    mount_checker: Optional[MountChecker] = None
    last_tick_runtime_at: float = 0.0
    last_queue_top_up_at: float = 0.0


class DualClientBotEngine:
    """
    Round-robin dual-client orchestrator.

    The engine executes one context tick at a time and only after foreground
    confirmation to avoid input bleed when clients overlap.
    """

    CRITICAL_ACTION_BUFFER_SEC = 0.65
    CONTEXT_STABILIZATION_SEC = 0.05
    POST_KILL_PRIORITY_SEC = 0.9
    MAX_RECALIBRATION_COOLDOWN_SEC = 8.0
    HP_RECALIBRATION_THRESHOLD = 5
    GHOST_IDLE_SLEEP_MIN_SEC = 0.30
    GHOST_IDLE_SLEEP_MAX_SEC = 0.50
    GHOST_IDLE_CHAIN_WINDOW_SEC = 1.0
    HP_ACQUIRE_TIMEOUT_SEC = 10.0
    HP_TIMEOUT_RECOVERY_MAX_ATTEMPTS = 4
    HP_TIMEOUT_RECOVERY_ROTATE_SEC = 0.30
    DEFAULT_REACHABLE_DISTANCE_PX = 420.0
    POST_HP_ANTISTUCK_MAX_TOTAL_SEC = 2.0
    POST_HP_ANTISTUCK_MIN_KEY_SEC = 0.08
    POST_HP_ANTISTUCK_MAX_KEY_SEC = 0.65
    PRE_CLICK_INPUT_SETTLE_SEC = 0.02
    QUEUE_INTER_CLICK_MIN_SEC = 0.02
    QUEUE_INTER_CLICK_MAX_SEC = 0.04
    CLICK_TRAVEL_MIN_SEC = 0.05
    CLICK_TRAVEL_MAX_SEC = 0.10
    CLICK_DOWN_UP_MIN_MS = 20
    CLICK_DOWN_UP_MAX_MS = 40
    CAPTCHA_MAX_ATTEMPTS = 3
    CAPTCHA_SOLVER_TIMEOUT_SEC = 8.5
    CAPTCHA_RETRY_COOLDOWN_SEC = 0.2
    CAPTCHA_VERIFY_WINDOW_SEC = 1.25
    SESSION_WORK_MIN_SEC = 3600.0
    SESSION_WORK_MAX_SEC = 7200.0
    SESSION_BREAK_MIN_SEC = 300.0
    SESSION_BREAK_MAX_SEC = 600.0
    SESSION_STATUS_EMIT_INTERVAL_SEC = 1.0
    DEFAULT_DEFERRED_QUEUE_CLICK_DELAY_SEC = 3.0
    DEFAULT_QUEUE_WAIT_GRACE_SEC = 2.5
    PASSIVE_TICK_INTERVAL_SEC = 0.16
    SNAP_ACTION_SHIFT_THRESHOLD_PX = 10.0
    SCENE_STABILIZATION_SAMPLE_GAP_SEC = 0.012
    SCENE_STABILIZATION_MAX_DELTA = 16.0
    QUEUE_TOP_UP_THRESHOLD = 2
    QUEUE_TOP_UP_COOLDOWN_SEC = 0.30
    BATCH_QUEUE_RELOCK_INTERVAL_SEC = 0.30
    DEFAULT_BATCH_QUEUE_STABILIZATION_SEC = 2.0
    CAPTCHA_MIN_FRAME_WIDTH = 320
    CAPTCHA_MIN_FRAME_HEIGHT = 180
    CAPTCHA_FOCUS_GRACE_SEC = 0.35
    CAPTCHA_CURSOR_PARK_POS = (0, 0)

    def __init__(self):
        self.driver: Optional[DriverBot] = None
        self.stop_event: Optional[threading.Event] = None
        self.log_callback: Optional[Callable[[str], None]] = None
        self.stats_callback: Optional[Callable[[int, float], None]] = None
        self.overlay_callback: Optional[Callable[[list], None]] = None
        self.overlay_hide_callback: Optional[Callable[[bool], None]] = None
        self.status_callback: Optional[Callable[[str], None]] = None

        self.process_manager = get_process_manager()
        self.contexts: List[BotContext] = []
        self._last_active_hwnd: Optional[int] = None
        self._last_combat_yield_slot: Optional[str] = None
        self._last_combat_yield_at: float = 0.0
        self._ghost_yield_pending: bool = False
        self._last_session_status_message: str = ""
        self._action_mutex = threading.Lock()
        self.is_global_captcha_panic: bool = False
        self._panic_context_slot: Optional[str] = None
        self._panic_captcha_rect: Optional[Dict[str, int]] = None
        self._panic_frame: Optional[np.ndarray] = None
        self.global_captcha_cfg: Dict[str, Any] = {
            "enabled": True,
            "api_key": "",
            "selected_model": DEFAULT_GEMINI_MODEL,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_bot_logic(
        self,
        mouse_id: int,
        client_contexts: List[Dict[str, Any]],
        global_config: Dict[str, Any],
        log_callback: Callable[[str], None],
        stop_event: threading.Event,
        stats_callback: Optional[Callable[[int, float], None]] = None,
        overlay_callback: Optional[Callable[[list], None]] = None,
        overlay_hide_callback: Optional[Callable[[bool], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Entrypoint for worker thread."""
        asyncio.run(
            self._async_run_bot_logic(
                mouse_id=mouse_id,
                client_contexts=client_contexts,
                global_config=global_config,
                log_callback=log_callback,
                stop_event=stop_event,
                stats_callback=stats_callback,
                overlay_callback=overlay_callback,
                overlay_hide_callback=overlay_hide_callback,
                status_callback=status_callback,
            )
        )

    # ------------------------------------------------------------------
    # Runtime scaffolding
    # ------------------------------------------------------------------

    async def _async_run_bot_logic(  # NOSONAR
        self,
        mouse_id: int,
        client_contexts: List[Dict[str, Any]],
        global_config: Dict[str, Any],
        log_callback: Callable[[str], None],
        stop_event: threading.Event,
        stats_callback: Optional[Callable[[int, float], None]] = None,
        overlay_callback: Optional[Callable[[list], None]] = None,
        overlay_hide_callback: Optional[Callable[[bool], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.stop_event = stop_event
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.overlay_callback = overlay_callback
        self.overlay_hide_callback = overlay_hide_callback
        self.status_callback = status_callback

        self._load_global_captcha_cfg(global_config)

        self._log("[ORCH] Dual-client engine starting...")

        try:
            self.driver = DriverBot(mouse_id=int(mouse_id), log_callback=self._log)
            self._log(f"[ORCH] Driver initialized (Mouse ID: {int(mouse_id)})")
            self.process_manager.set_input_flush_callback(self.driver.release_all_inputs)
        except Exception as driver_error:
            self._log(f"[ORCH] [ERROR] Driver init failed: {driver_error}")
            return

        self.contexts = self._initialize_contexts(client_contexts)
        if not self.contexts:
            self._log("[ORCH] [ERROR] No valid client contexts initialized.")
            self._cleanup()
            return

        self._log(
            "[ORCH] Contexts ready: " + ", ".join([f"{ctx.slot}(HWND={ctx.hwnd})" for ctx in self.contexts])
        )

        self._log("[ORCH] Event-driven zero-idle orchestration active. F12 = failsafe stop.")

        try:
            while not self.stop_event.is_set():
                if keyboard.is_pressed("F12"):
                    self._log("[ORCH] Emergency stop triggered (F12)")
                    break

                if self.is_global_captcha_panic:
                    await self._run_global_captcha_interrupt()
                    continue

                if self._poll_global_captcha_panic():
                    continue

                schedule = self._build_schedule()
                for context in schedule:
                    if self.is_global_captcha_panic:
                        break

                    if self.stop_event.is_set():
                        break

                    if not self._activate_context(context):
                        if self.is_global_captcha_panic:
                            break
                        continue

                    lock_delay = await self._tick_context(context)
                    if self.is_global_captcha_panic:
                        break

                    if lock_delay > 0:
                        self._sleep_with_failsafe(lock_delay)
                        # Critical action lock: do not switch away immediately.
                        break

                ghost_delay = self._consume_ghost_yield_delay()
                if ghost_delay > 0:
                    self._sleep_with_failsafe(ghost_delay)
                else:
                    await asyncio.sleep(0.01)

        except KeyboardInterrupt:
            self._log("[ORCH] Interrupted by user")
        except Exception as runtime_error:
            self._log(f"[ORCH] [CRITICAL] {runtime_error}")
        finally:
            self._emit_overlay([])
            self._cleanup()
            self._log("[ORCH] Dual-client engine stopped")

    def _initialize_contexts(self, client_contexts: List[Dict[str, Any]]) -> List[BotContext]:
        contexts: List[BotContext] = []

        for raw in client_contexts:
            slot = str(raw.get("slot", "")).strip() or "client_1"
            hwnd = int(raw.get("hwnd", 0) or 0)
            config = raw.get("config", {}) if isinstance(raw.get("config", {}), dict) else {}
            config = dict(config)

            if hwnd <= 0:
                self._log(f"[ORCH] [WARN] Skipping {slot}: invalid HWND")
                continue

            skills_cfg = config.get("skills", {}) if isinstance(config.get("skills", {}), dict) else {}
            active_profile = str(skills_cfg.get("active_profile", "savasci_bedensel")).strip() or "savasci_bedensel"

            general_cfg = dict(config.get("general", {})) if isinstance(config.get("general", {}), dict) else {}
            general_cfg["captcha_solver"] = bool(self.global_captcha_cfg.get("enabled", True))
            config["general"] = general_cfg

            context = BotContext(
                slot=slot,
                hwnd=hwnd,
                config=config,
                active_profile=active_profile,
            )

            # Per-context manager instantiation for strict state isolation.
            context.capturer = WindowCapture(target_hwnd=hwnd)
            context.vision = YoloVision()
            context.skill_manager = SkillManager()
            context.inventory_manager = InventoryManager()
            context.quest_handler = QuestHandler(target_hwnd=hwnd)
            context.mount_checker = MountChecker()

            yolo_conf = float(self._cfg(context, "vision", "yolo_confidence", 0.45))
            context.vision.set_confidence_threshold(yolo_conf)

            try:
                context.skill_manager.configure(skills_cfg, log_callback=lambda m, c=context: self._log_ctx(c, m))
                context.skill_manager.is_mounted = True
            except Exception as skill_error:
                self._log_ctx(context, f"[SKILL] [WARN] Configure failed: {skill_error}")

            self._reset_context_session_timer(context)

            contexts.append(context)

        # Stable order: client_1, client_2, then extras.
        contexts.sort(key=lambda c: c.slot)
        return contexts

    def update_context_skill_profile(self, slot: str, profile_name: str) -> bool:
        slot_key = str(slot).strip()
        profile = str(profile_name).strip()
        if not slot_key or not profile:
            return False

        for context in self.contexts:
            if context.slot != slot_key:
                continue

            if not context.skill_manager:
                return False

            skills_cfg = context.config.setdefault("skills", {})
            if not isinstance(skills_cfg, dict):
                skills_cfg = {}
                context.config["skills"] = skills_cfg

            skills_cfg["active_profile"] = profile
            context.active_profile = profile

            if hasattr(context.skill_manager, "configure"):
                context.skill_manager.configure(
                    skills_cfg,
                    log_callback=lambda message, c=context: self._log_ctx(c, message),
                )

            context.skill_manager.load_profile_templates(
                profile,
                log_callback=lambda message, c=context: self._log_ctx(c, message),
            )
            context.queued_stone_ids.clear()
            return True

        return False

    def _build_schedule(self) -> List[BotContext]:
        if not self.contexts:
            return []

        if self.is_global_captcha_panic and self._panic_context_slot:
            panic_context = next((ctx for ctx in self.contexts if ctx.slot == self._panic_context_slot), None)
            if panic_context is not None:
                return [panic_context]

        now = time.time()

        # Strict atomicity: never schedule another context while a queued
        # multi-click burst is still running.
        for ctx in self.contexts:
            if ctx.multi_click_lock_active:
                return [ctx]

        # Captcha context is always the highest urgency.
        captcha_priority = [
            ctx
            for ctx in self.contexts
            if ctx.captcha_active or ctx.state == BotState.SOLVING_CAPTCHA
        ]
        if captcha_priority:
            others = [ctx for ctx in self.contexts if ctx not in captcha_priority]
            return captcha_priority + others

        ready_contexts: List[BotContext] = []
        passive_contexts: List[BotContext] = []
        for ctx in self.contexts:
            is_passive = self._is_passive_context(ctx, now)
            if is_passive and (now - ctx.last_tick_runtime_at) < self.PASSIVE_TICK_INTERVAL_SEC:
                continue

            if is_passive:
                passive_contexts.append(ctx)
            else:
                ready_contexts.append(ctx)

        # Keep active contexts first; passive contexts are still ticked but throttled.
        ready_contexts.extend(passive_contexts)

        # If one client is actively attacking, bias toward making the other client
        # acquire/maintain engagement.
        combat_slots = {ctx.slot for ctx in ready_contexts if ctx.state == BotState.COMBAT}
        if combat_slots:
            prioritized: List[BotContext] = []
            trailing: List[BotContext] = []
            for ctx in ready_contexts:
                if ctx.slot in combat_slots and ctx.state == BotState.COMBAT:
                    trailing.append(ctx)
                else:
                    prioritized.append(ctx)
            if prioritized:
                ready_contexts = prioritized + trailing

        return ready_contexts

    def _is_passive_context(self, context: BotContext, now: float) -> bool:
        if now < context.action_locked_until:
            return True

        if context.state == BotState.COMBAT:
            return True

        if context.state == BotState.EXECUTING_QUEUE:
            return True

        if context.state == BotState.MOVING_TO_TARGET:
            if context.hp_acquire_deadline > now:
                return True

        if (
            context.state == BotState.QUEUE_WAIT
            and context.queued_targets_count > 0
            and now < context.queue_wait_until
        ):
            return True

        return False

    def _is_context_captcha_solver_enabled(self, context: BotContext) -> bool:
        global_captcha_enabled = bool(self.global_captcha_cfg.get("enabled", True))
        local_captcha_enabled = bool(self._cfg(context, "general", "captcha_solver", True))
        return global_captcha_enabled and local_captcha_enabled

    def _check_and_trigger_global_captcha(self, context: BotContext, frame: np.ndarray) -> bool:
        if not self._is_context_captcha_solver_enabled(context):
            return False

        if not isinstance(frame, np.ndarray):
            return False

        frame_h, frame_w = frame.shape[:2]
        if frame_w < self.CAPTCHA_MIN_FRAME_WIDTH or frame_h < self.CAPTCHA_MIN_FRAME_HEIGHT:
            return False

        try:
            captcha_detected, captcha_rect = Vision.check_for_captcha(frame)
        except Exception as detection_error:
            self._log_ctx(context, f"[CAPTCHA] [WARN] Detection failed: {detection_error}")
            return False

        if not captcha_detected:
            context.captcha_active = False
            return False

        self._set_global_captcha_panic(context, frame, captcha_rect)
        return True

    def _poll_global_captcha_panic(self) -> bool:
        if self.is_global_captcha_panic:
            return True

        for context in self.contexts:
            if not context.capturer:
                continue

            if not self._is_context_captcha_solver_enabled(context):
                continue

            frame = context.capturer.capture_frame()
            if frame is None:
                continue

            frame_h, frame_w = frame.shape[:2]
            if frame_w < self.CAPTCHA_MIN_FRAME_WIDTH or frame_h < self.CAPTCHA_MIN_FRAME_HEIGHT:
                continue

            try:
                captcha_detected, captcha_rect = Vision.check_for_captcha(frame)
            except Exception:
                continue

            if not captcha_detected:
                continue

            self._set_global_captcha_panic(context, frame, captcha_rect)
            return True

        return False

    def _set_global_captcha_panic(
        self,
        context: BotContext,
        frame: np.ndarray,
        captcha_rect: Optional[Dict[str, int]],
    ) -> None:
        self.is_global_captcha_panic = True
        self._panic_context_slot = context.slot
        self._panic_frame = frame
        self._panic_captcha_rect = None

        if captcha_rect:
            self._panic_captcha_rect = {
                "left": int(captcha_rect.get("left", 0)),
                "top": int(captcha_rect.get("top", 0)),
                "width": int(captcha_rect.get("width", 0)),
                "height": int(captcha_rect.get("height", 0)),
            }

        context.captcha_active = True
        context.captcha_rect_cache = self._panic_captcha_rect
        self._set_state(context, BotState.SOLVING_CAPTCHA)

        for other in self.contexts:
            if other.slot == context.slot:
                continue
            other.multi_click_lock_active = False

        if self.driver:
            try:
                self.driver.release_all_inputs()
            except Exception:
                pass

        if self._action_mutex.locked():
            try:
                self._action_mutex.release()
            except Exception:
                pass

        self._log_ctx(context, "[CAPTCHA] GLOBAL PANIC engaged. Stop-the-world interrupt activated.")

        # Immediately clear and HIDE overlay so it doesn't pollute MSS captures.
        self._emit_overlay([])
        self._set_overlay_hidden(True)

    def _force_captcha_window_foreground(self, context: BotContext) -> bool:
        """Force the CAPTCHA-afflicted window to OS foreground and wait for draw grace.

        Returns True if the window was successfully brought to the foreground.
        """
        import win32gui
        import win32con

        hwnd = context.hwnd
        if not hwnd:
            return False

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        # Aggressive focus forcing via ProcessManager (AttachThreadInput + multi-API).
        switched = self.process_manager.switch_context(hwnd, timeout_ms=800)
        if not switched:
            self._log_ctx(context, "[CAPTCHA] [WARN] switch_context failed, attempting force_focus_fallback")
            try:
                self.process_manager._force_focus_fallback(hwnd)
            except Exception:
                pass

        # OS draw grace period: Windows needs ~300-400ms to fully composite the
        # newly-foregrounded window and clear ghosting from the previous one.
        # Also park the mouse cursor outside the game area so it doesn't
        # appear in the CAPTCHA screenshot sent to Gemini.
        try:
            import ctypes
            park_x, park_y = self.CAPTCHA_CURSOR_PARK_POS
            ctypes.windll.user32.SetCursorPos(int(park_x), int(park_y))
        except Exception:
            pass
        time.sleep(self.CAPTCHA_FOCUS_GRACE_SEC)

        # Verify the window actually reached foreground.
        try:
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    async def _run_global_captcha_interrupt(self) -> None:
        if not self.is_global_captcha_panic:
            return

        panic_slot = self._panic_context_slot
        context = next((ctx for ctx in self.contexts if ctx.slot == panic_slot), None)
        if context is None:
            self.is_global_captcha_panic = False
            self._panic_context_slot = None
            self._panic_captcha_rect = None
            self._panic_frame = None
            return

        # ── Step 1: Force the CAPTCHA window to physical foreground ──
        # This is the ABSOLUTE FIRST action: the window must be on top
        # and fully rendered before any screenshot or input.
        if not self._force_captcha_window_foreground(context):
            self._log_ctx(context, "[CAPTCHA] [WARN] Could not bring window to foreground, retrying next tick")
            await asyncio.sleep(0.02)
            return

        if not context.capturer:
            self.is_global_captcha_panic = False
            return

        px, py, pw, ph = context.capturer.get_client_area_geometry()
        game_region = {"left": px, "top": py, "width": pw, "height": ph}

        # ── Step 2: Discard stale panic frame, take a FRESH capture ──
        # The _panic_frame was captured before the window was foregrounded
        # and is potentially occluded/corrupted. Always re-capture.
        self._panic_frame = None
        frame = context.capturer.capture_frame()
        if frame is None:
            await asyncio.sleep(0.02)
            return

        captcha_rect = self._panic_captcha_rect
        if captcha_rect is None:
            detected, fresh_rect = Vision.check_for_captcha(frame)
            if not detected:
                self._reset_all_contexts_after_global_captcha()
                return
            if fresh_rect:
                captcha_rect = {
                    "left": int(fresh_rect.get("left", 0)),
                    "top": int(fresh_rect.get("top", 0)),
                    "width": int(fresh_rect.get("width", 0)),
                    "height": int(fresh_rect.get("height", 0)),
                }

        if captcha_rect is None:
            await asyncio.sleep(0.02)
            return

        solved = await self._solve_captcha_once(context, captcha_rect, game_region, frame)
        if not solved:
            if self.stop_event and self.stop_event.is_set():
                return
            await asyncio.sleep(0.02)
            return

        verify_frame = context.capturer.capture_frame()
        if verify_frame is not None:
            still_visible, _ = Vision.check_for_captcha(verify_frame, threshold=0.66)
            if still_visible:
                await asyncio.sleep(0.02)
                return

        self._reset_all_contexts_after_global_captcha()

    def _reset_all_contexts_after_global_captcha(self) -> None:
        self.is_global_captcha_panic = False
        self._panic_context_slot = None
        self._panic_captcha_rect = None
        self._panic_frame = None

        # Restore overlay visibility that was hidden during panic.
        self._set_overlay_hidden(False)

        for context in self.contexts:
            context.captcha_active = False
            context.captcha_solver_inflight = False
            context.captcha_solution_index = None
            context.captcha_rect_cache = None
            context.captcha_grid_origin = None
            context.captcha_verify_deadline = 0.0
            context.captcha_button_clicked = False
            context.captcha_cooldown_until = 0.0
            context.captcha_wait_until = 0.0
            context.captcha_attempts = 0
            context.captcha_request_token += 1
            context.multi_click_lock_active = False

            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            context.hp_acquire_deadline = 0.0
            context.queued_stone_ids.clear()
            context.post_kill_priority_until = 0.0
            context.post_kill_reacquire_pending = False
            context.last_clicked_pos = None

            self._set_state(context, BotState.SEARCHING)

    def _load_global_captcha_cfg(self, global_config: Dict[str, Any]) -> None:
        defaults: Dict[str, Any] = {
            "enabled": True,
            "api_key": "",
            "selected_model": DEFAULT_GEMINI_MODEL,
        }

        if isinstance(global_config, dict):
            captcha_cfg = global_config.get("captcha", {})
            if isinstance(captcha_cfg, dict):
                defaults["enabled"] = bool(captcha_cfg.get("enabled", True))
                defaults["api_key"] = str(captcha_cfg.get("api_key", ""))
                model_name = str(captcha_cfg.get("selected_model", DEFAULT_GEMINI_MODEL)).strip()
                defaults["selected_model"] = model_name or DEFAULT_GEMINI_MODEL

        self.global_captcha_cfg = defaults

    def _captcha_pick_local_click_point(
        self,
        captcha_rect: Dict[str, int],
        image_index: int,
        frame_shape: Tuple[int, ...],
        grid_origin: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
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

    def _activate_context(self, context: BotContext) -> bool:
        switched = self.process_manager.switch_context(context.hwnd, timeout_ms=500)
        if not switched:
            self._log_ctx(context, "[SWITCH] [WARN] Foreground switch timeout; skipping tick.")
            return False

        context_changed = self._last_active_hwnd != context.hwnd
        if context_changed:
            # DirectX/OpenGL front buffer needs a short settle period after focus switch.
            time.sleep(self.CONTEXT_STABILIZATION_SEC)

        if context.capturer and context.capturer.target_hwnd != context.hwnd:
            context.capturer.update_target_hwnd(context.hwnd)

        # On every client switch, CAPTCHA must be checked before any other logic.
        skip_captcha_check_for_panic_context = (
            self.is_global_captcha_panic
            and self._panic_context_slot == context.slot
        )

        if (
            context.capturer
            and self._is_context_captcha_solver_enabled(context)
            and not skip_captcha_check_for_panic_context
        ):
            switch_frame = context.capturer.capture_frame()
            if switch_frame is not None and self._check_and_trigger_global_captcha(context, switch_frame):
                return False

        if context_changed:
            if context.vision:
                context.vision.clear_runtime_buffers()
            self._emit_overlay([])
            self._last_active_hwnd = context.hwnd

        return True

    # ------------------------------------------------------------------
    # Single context tick
    # ------------------------------------------------------------------

    async def _tick_context(self, context: BotContext) -> float:  # NOSONAR
        now = time.time()
        context.last_tick_runtime_at = now

        if self._handle_context_session_break(context, now):
            self._clear_combat_yield_candidate(context)
            self._emit_stats()
            return 0.0

        if not context.capturer:
            self._log_ctx(context, "[CAPTURE] [ERROR] Capture engine missing")
            self._clear_combat_yield_candidate(context)
            return 0.1

        if not context.capturer.is_window_focused():
            self._log_ctx(context, "[WAIT] Focus gate blocked actions")
            self._emit_overlay([])
            self._clear_combat_yield_candidate(context)
            return 0.1

        frame = context.capturer.capture_frame()
        if frame is None:
            self._clear_combat_yield_candidate(context)
            return 0.05

        gx, gy, gw, gh = context.capturer.get_client_area_geometry()
        game_region = {
            "left": gx,
            "top": gy,
            "width": gw,
            "height": gh,
        }

        # Priority 0: Global CAPTCHA panic trigger
        if self._check_and_trigger_global_captcha(context, frame):
            self._clear_combat_yield_candidate(context)
            self._emit_stats()
            return 0.0

        if now < context.action_locked_until:
            self._clear_combat_yield_candidate(context)
            return max(0.0, context.action_locked_until - now)

        # Priority 1: Death handling
        is_dead, dead_pos = Vision.check_if_dead(frame, game_region)
        if is_dead and dead_pos:
            self._log_ctx(context, "[DEATH] Detected, running recovery")
            lock = self._handle_death_protocol(context, game_region, dead_pos)
            context.queued_targets_count = 0
            context.queued_stone_ids.clear()
            context.hp_acquire_deadline = 0.0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            context.post_kill_priority_until = 0.0
            context.post_kill_reacquire_pending = False
            self._set_state(context, BotState.SEARCHING)
            self._clear_combat_yield_candidate(context)
            self._emit_stats()
            return lock

        # Priority 2: Combat engine
        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)
        combat_delay = 0.0

        if hp_visible:
            entering_from_moving = context.state == BotState.MOVING_TO_TARGET

            if entering_from_moving and context.deferred_queue_remaining > 0:
                if context.deferred_queue_click_at <= 0.0:
                    context.deferred_queue_click_at = now + self._deferred_queue_click_delay_sec(context)

                if now < context.deferred_queue_click_at:
                    self._clear_combat_yield_candidate(context)
                    self._emit_stats()
                    return 0.0

                self._execute_deferred_queue_clicks(context)

            if context.state not in (BotState.COMBAT, BotState.EXECUTING_QUEUE):
                context.combat_started_at = now
                context.combat_hp_missing_count = 0
                context.last_strafe_at = now
                context.combat_last_z_at = now

            context.hp_acquire_deadline = 0.0
            context.queue_wait_until = 0.0
            if context.queued_targets_count > 1:
                self._set_state(context, BotState.EXECUTING_QUEUE)
            else:
                self._set_state(context, BotState.COMBAT)

            # Never run anti-stuck WASD unless HP lock happened while moving to a clicked target.
            if entering_from_moving:
                self._run_post_hp_antistuck_burst(context)

            self._maybe_top_up_queue(context, frame)
            self._mark_combat_yield(context)
        else:
            self._clear_combat_yield_candidate(context)

            if context.hp_acquire_deadline > 0.0 and now >= context.hp_acquire_deadline:
                self._register_no_hp_failure(context, reason="hp_acquire_timeout")
                pydirectinput.press("s")

                context.hp_acquire_deadline = 0.0
                context.queued_targets_count = 0
                context.deferred_queue_remaining = 0
                context.deferred_queue_click_at = 0.0
                context.queue_wait_until = 0.0
                context.queued_stone_ids.clear()
                context.post_kill_priority_until = 0.0
                context.post_kill_reacquire_pending = False

                if context.state != BotState.SEARCHING:
                    self._set_state(context, BotState.SEARCHING)

                reacquired = self._recover_target_before_yield(context, game_region)
                if reacquired:
                    self._log_ctx(context, "[MOVE] HP lock timeout recovery succeeded. New target clicked.")
                    self._emit_stats()
                    return 0.0

                self._log_ctx(
                    context,
                    f"[MOVE] HP lock not acquired within {int(self.HP_ACQUIRE_TIMEOUT_SEC)}s. Yielding to next client.",
                )
                self._emit_stats()
                return 0.0

            # Queue dedupe window ends as soon as HP bar disappears.
            if context.queued_stone_ids:
                context.queued_stone_ids.clear()

            if context.state in (BotState.COMBAT, BotState.EXECUTING_QUEUE):
                context.stones_destroyed += 1
                context.last_kill_at = now
                context.failed_click_count = 0
                context.last_clicked_pos = None

                if bool(self._cfg(context, "general", "auto_loot", True)):
                    pydirectinput.press("z", presses=6, interval=0.02)

                context.queued_targets_count = 0
                context.deferred_queue_remaining = 0
                context.deferred_queue_click_at = 0.0
                context.queue_wait_until = 0.0
                context.post_kill_priority_until = 0.0
                context.post_kill_reacquire_pending = False
                self._set_state(context, BotState.SEARCHING)
                self._log_ctx(context, f"[COMBAT] HP bar lost, target cleared. Total={context.stones_destroyed}")

            if context.state in (BotState.MOVING_TO_TARGET, BotState.EXECUTING_QUEUE):
                combat_delay = max(combat_delay, self._state_moving_to_target(context, frame, game_region))
                if context.state == BotState.SEARCHING:
                    combat_delay = max(combat_delay, self._state_searching(context, frame, game_region))
            else:
                if context.state != BotState.SEARCHING:
                    self._set_state(context, BotState.SEARCHING)
                combat_delay = max(combat_delay, self._state_searching(context, frame, game_region))

        # Priority 3: Skill check (post-combat actions)
        if self._run_priority_skill_check(context, frame):
            self._log_ctx(context, "[SKILL] Post-combat refresh complete.")

        # Priority 4: Yield (non-critical jobs stay last)
        extra_delay = self._run_non_critical_tasks(context, frame, game_region)
        lock_delay = max(combat_delay, extra_delay)
        self._emit_stats()
        return lock_delay

    # ------------------------------------------------------------------
    # State machine steps (one action per tick)
    # ------------------------------------------------------------------

    def _state_searching(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> float:  # NOSONAR
        if not context.vision or not context.capturer:
            return 0.0

        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)
        if hp_visible:
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            return 0.0

        queue_enabled = bool(self._cfg(context, "combat", "multi_target_queue_enabled", False))
        try:
            configured_queue_size = int(self._cfg(context, "combat", "multi_target_queue_size", 3))
        except Exception:
            configured_queue_size = 3

        queue_size = max(1, configured_queue_size)
        max_targets = queue_size if queue_enabled else 1

        local_target_positions, detections = self._get_top_targets(context, frame, max_targets=max_targets)
        overlay_detections = self._to_overlay_detections(detections)
        self._emit_overlay(overlay_detections)

        now = time.time()
        if now - context.last_heartbeat_at >= 2.0:
            self._log_ctx(
                context,
                (
                    f"[SEARCH] detections={len(detections)} "
                    f"target_count={len(local_target_positions)} "
                    "mode=acquire"
                ),
            )
            context.last_heartbeat_at = now

        if not local_target_positions:
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            if now - context.last_roam_at >= 0.45:
                self._atomic_key_hold("q", 0.2)
                context.last_roam_at = now
            return 0.0

        first_target = local_target_positions[0]
        first_anchor_x, first_anchor_y = int(first_target[0]), int(first_target[1])
        first_anchor_global_x, first_anchor_global_y = context.capturer.get_screen_position(first_anchor_x, first_anchor_y)

        reachable_threshold_px = self._reachable_distance_px(context)
        closest_distance_px = self._target_distance_px(first_target, detections, frame)
        if closest_distance_px > reachable_threshold_px:
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            if now - context.last_roam_at >= 0.45:
                self._log_ctx(
                    context,
                    (
                        "[SEARCH] Closest target is outside reachable threshold "
                        f"(dist={closest_distance_px:.1f}px > limit={reachable_threshold_px:.1f}px)."
                    ),
                )
                self._atomic_key_hold("q", 0.2)
                context.last_roam_at = now
            return 0.0

        if self._is_repeated_target(context, first_anchor_global_x, first_anchor_global_y, game_region):
            if context.failed_click_count > 5:
                self._log_ctx(context, "[SEARCH] Repeated target loop detected, rotating camera")
                self._atomic_key_hold("q", 0.35)
                context.failed_click_count = 0
                context.last_clicked_pos = None
            return 0.0

        clicked_count = self._batch_queue_from_search(
            context=context,
            frame=frame,
            local_targets=local_target_positions,
            detections=detections,
            queue_limit=queue_size,
        )
        if clicked_count <= 0:
            if context.state == BotState.BATCH_QUEUEING:
                self._set_state(context, BotState.SEARCHING)
            return 0.0

        context.queued_targets_count = clicked_count
        context.deferred_queue_remaining = 0
        context.deferred_queue_click_at = 0.0
        context.hp_acquire_deadline = time.time() + self.HP_ACQUIRE_TIMEOUT_SEC
        context.queue_wait_until = 0.0
        context.movement_started_at = time.time()
        context.last_strafe_at = context.movement_started_at
        self._set_state(context, BotState.MOVING_TO_TARGET)
        self._log_ctx(context, f"[QUEUE] Batch queued {clicked_count} target(s). Waiting for HP lock.")

        return 0.0

    def _batch_queue_from_search(
        self,
        context: BotContext,
        frame: np.ndarray,
        local_targets: List[Tuple[int, int]],
        detections: List[Dict[str, Any]],
        queue_limit: int,
    ) -> int:
        if not context.capturer:
            return 0

        if not local_targets:
            return 0

        if not self._action_mutex.acquire(blocking=False):
            return 0

        self._set_state(context, BotState.BATCH_QUEUEING)
        context.multi_click_lock_active = True
        clicked_count = 0
        batch_started_at = time.time()
        stabilization_wait_done = False

        try:
            self._flush_input_barrier(context, reason="pre_batch_queueing")
            self._sleep_with_failsafe(self.PRE_CLICK_INPUT_SETTLE_SEC)

            max_clicks = max(1, min(queue_limit, len(local_targets)))
            active_targets = list(local_targets)
            active_detections = list(detections)
            active_frame = frame

            for idx in range(max_clicks):
                if self.is_global_captcha_panic:
                    break

                if idx >= len(active_targets):
                    break

                if idx > 0:
                    self._sleep_with_failsafe(
                        random.uniform(self.QUEUE_INTER_CLICK_MIN_SEC, self.QUEUE_INTER_CLICK_MAX_SEC)
                    )

                if time.time() - batch_started_at >= self.BATCH_QUEUE_RELOCK_INTERVAL_SEC:
                    refreshed = context.capturer.capture_frame()
                    if refreshed is not None:
                        refreshed_targets, refreshed_detections = self._get_top_targets(
                            context,
                            refreshed,
                            max_targets=max_clicks,
                        )
                        if refreshed_targets:
                            active_targets = refreshed_targets
                            active_detections = refreshed_detections
                            active_frame = refreshed

                if self._check_and_trigger_global_captcha(context, active_frame):
                    break

                if idx >= len(active_targets):
                    break

                batch_started_at = time.time()

                base_target = active_targets[idx]
                step_target, step_detections = self._resolve_queue_step_target(
                    context=context,
                    base_targets=active_targets,
                    base_detections=active_detections,
                    max_targets=max_clicks,
                    step_index=idx,
                )
                target = step_target if step_target is not None else (int(base_target[0]), int(base_target[1]))
                dets = step_detections if step_detections else active_detections

                resolved_click = self._resolve_safe_click_local(context, active_frame, target, dets)
                if resolved_click is None:
                    continue

                click_local_x, click_local_y = resolved_click
                click_global_x, click_global_y = context.capturer.get_screen_position(click_local_x, click_local_y)

                if not self._human_click(context, click_global_x, click_global_y, assume_mutex_held=True):
                    continue

                target_anchor_x, target_anchor_y = int(target[0]), int(target[1])
                anchor_global_x, anchor_global_y = context.capturer.get_screen_position(target_anchor_x, target_anchor_y)
                context.last_clicked_pos = (anchor_global_x, anchor_global_y)
                context.queued_stone_ids.add(self._target_identity(target))
                clicked_count += 1

                if clicked_count == 1 and max_clicks > 1 and not stabilization_wait_done:
                    stabilization_wait_done = True
                    self._sleep_with_failsafe(self._batch_queue_stabilization_sec(context))
                    if self.is_global_captcha_panic:
                        break

                    refreshed_after_wait = context.capturer.capture_frame()
                    if refreshed_after_wait is not None:
                        refreshed_targets, refreshed_detections = self._get_top_targets(
                            context,
                            refreshed_after_wait,
                            max_targets=max_clicks,
                        )
                        if refreshed_targets:
                            active_targets = refreshed_targets
                            active_detections = refreshed_detections
                            active_frame = refreshed_after_wait

                    if idx + 1 >= len(active_targets):
                        # Queue can shrink during stabilization wait; exit safely.
                        break

            self._flush_input_barrier(context, reason="post_batch_queueing")
        finally:
            context.multi_click_lock_active = False
            try:
                self._action_mutex.release()
            except Exception:
                pass

        return clicked_count

    def _maybe_top_up_queue(self, context: BotContext, frame: np.ndarray) -> None:
        if not context.capturer or not context.vision:
            return

        queue_enabled = bool(self._cfg(context, "combat", "multi_target_queue_enabled", False))
        if not queue_enabled:
            return

        now = time.time()
        if (now - context.last_queue_top_up_at) < self.QUEUE_TOP_UP_COOLDOWN_SEC:
            return

        try:
            configured_queue_size = int(self._cfg(context, "combat", "multi_target_queue_size", 3))
        except Exception:
            configured_queue_size = 3

        queue_size = max(1, configured_queue_size)
        if context.queued_targets_count > self.QUEUE_TOP_UP_THRESHOLD:
            return

        desired = max(0, queue_size - context.queued_targets_count)
        if desired <= 0:
            return

        added = self._queue_targets_while_attacking(context, frame, desired_clicks=desired)
        if added <= 0:
            return

        context.queued_targets_count = min(queue_size, context.queued_targets_count + added)
        self._set_state(context, BotState.EXECUTING_QUEUE)
        context.last_queue_top_up_at = time.time()

    def _resolve_primary_target_for_click(
        self,
        context: BotContext,
        base_target: Tuple[int, int],
        base_detections: List[Dict[str, Any]],
    ) -> Tuple[Tuple[int, int], List[Dict[str, Any]]]:
        selected_target = (int(base_target[0]), int(base_target[1]))

        if not context.capturer or not context.vision:
            return selected_target, base_detections

        refreshed_frame = context.capturer.capture_frame()
        if refreshed_frame is None:
            return selected_target, base_detections

        refreshed_targets, refreshed_detections = self._get_top_targets(context, refreshed_frame, max_targets=1)
        if not refreshed_targets:
            return selected_target, base_detections

        refreshed_target = refreshed_targets[0]
        refreshed_xy = (int(refreshed_target[0]), int(refreshed_target[1]))
        if self._target_shift_px(selected_target, refreshed_xy) <= self.SNAP_ACTION_SHIFT_THRESHOLD_PX:
            return selected_target, base_detections

        self._log_ctx(
            context,
            (
                "[SNAP] Primary target shifted "
                f"({self._target_shift_px(selected_target, refreshed_xy):.1f}px), re-locking coordinates."
            ),
        )
        return refreshed_xy, refreshed_detections

    def _target_shift_px(self, base_target: Tuple[int, int], refreshed_target: Tuple[int, int]) -> float:
        base_x, base_y = int(base_target[0]), int(base_target[1])
        new_x, new_y = int(refreshed_target[0]), int(refreshed_target[1])
        return math.hypot(float(new_x - base_x), float(new_y - base_y))

    def _is_scene_stable(self, context: BotContext) -> bool:
        if not context.capturer:
            return False

        first = context.capturer.capture_frame()
        if first is None:
            return False

        self._sleep_with_failsafe(self.SCENE_STABILIZATION_SAMPLE_GAP_SEC)
        second = context.capturer.capture_frame()
        if second is None:
            return False

        if first.shape != second.shape:
            return False

        # Scene-stability gate: avoid queue clicks while camera still rotates heavily.
        delta = float(np.mean(np.abs(second.astype(np.int16) - first.astype(np.int16))))
        return delta <= self.SCENE_STABILIZATION_MAX_DELTA

    def _target_identity(self, target: Tuple[int, int]) -> Tuple[int, int]:
        # Quantize local coordinates to absorb tiny camera jitter between frames.
        return (int(round(float(target[0]) / 12.0)), int(round(float(target[1]) / 12.0)))

    def _targets_are_close(self, left: Tuple[int, int], right: Tuple[int, int], tolerance_px: int = 24) -> bool:
        return (
            abs(int(left[0]) - int(right[0])) <= tolerance_px
            and abs(int(left[1]) - int(right[1])) <= tolerance_px
        )

    def _queue_targets_while_attacking(self, context: BotContext, frame: np.ndarray, desired_clicks: Optional[int] = None) -> int:
        if not context.vision or not context.capturer:
            return 0

        queue_enabled = bool(self._cfg(context, "combat", "multi_target_queue_enabled", False))
        if not queue_enabled:
            return 0

        try:
            configured_queue_size = int(self._cfg(context, "combat", "multi_target_queue_size", 3))
        except Exception:
            configured_queue_size = 3

        queue_size = max(1, configured_queue_size)
        local_targets, detections = self._get_top_targets(
            context,
            frame,
            max_targets=max(2, queue_size + 1),
        )
        self._emit_overlay(self._to_overlay_detections(detections))

        if len(local_targets) <= 1:
            return 0

        frame_h, frame_w = frame.shape[:2]
        anchor_x = float(frame_w) * 0.5
        anchor_y = float(frame_h) * 0.6

        active_target = min(
            local_targets,
            key=lambda item: ((float(item[0]) - anchor_x) ** 2 + (float(item[1]) - anchor_y) ** 2),
        )
        active_target_xy = (int(active_target[0]), int(active_target[1]))

        remaining_targets: List[Tuple[int, int]] = []
        for target in local_targets:
            candidate = (int(target[0]), int(target[1]))
            if self._targets_are_close(candidate, active_target_xy):
                continue

            candidate_id = self._target_identity(candidate)
            if candidate_id in context.queued_stone_ids:
                continue

            remaining_targets.append(candidate)
            if len(remaining_targets) >= queue_size:
                break

        if not remaining_targets:
            return 0

        if not self._action_mutex.acquire(blocking=False):
            return 0

        clicked_count = 0
        context.multi_click_lock_active = True
        try:
            max_clicks = len(remaining_targets)
            if desired_clicks is not None:
                max_clicks = min(max_clicks, max(0, int(desired_clicks)))

            for idx, target in enumerate(remaining_targets):
                if self.is_global_captcha_panic:
                    break

                if idx >= max_clicks:
                    break

                if idx > 0:
                    self._sleep_with_failsafe(
                        random.uniform(self.QUEUE_INTER_CLICK_MIN_SEC, self.QUEUE_INTER_CLICK_MAX_SEC)
                    )

                resolved_click = self._resolve_safe_click_local(context, frame, target, detections)
                if self._check_and_trigger_global_captcha(context, frame):
                    break
                if resolved_click is None:
                    continue
                click_local_x, click_local_y = resolved_click
                click_global_x, click_global_y = context.capturer.get_screen_position(click_local_x, click_local_y)

                if not self._human_click(context, click_global_x, click_global_y, assume_mutex_held=True):
                    continue

                context.queued_stone_ids.add(self._target_identity(target))
                clicked_count += 1
        finally:
            context.multi_click_lock_active = False
            try:
                self._action_mutex.release()
            except Exception:
                pass

        if clicked_count > 0:
            context.queued_targets_count = 1 + clicked_count
            self._flush_input_barrier(context, reason="combat_queue_clicks")
            self._log_ctx(context, f"[QUEUE] Combat queue appended {clicked_count} target(s).")

        return clicked_count

    def _state_moving_to_target(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> float:  # NOSONAR
        now = time.time()
        elapsed = now - context.movement_started_at

        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)
        if hp_visible:
            context.no_hp_click_failures = 0
            context.hp_acquire_deadline = 0.0
            self._set_state(context, BotState.VERIFY_ATTACK)
            self._log_ctx(context, "[MOVE] HP bar acquired")
            return 0.0

        if context.hp_acquire_deadline > 0.0 and now < context.hp_acquire_deadline:
            return 0.0

        miss_timeout = min(3.0, float(self._cfg(context, "general", "miss_timeout", 3.0)))
        movement_timeout = float(self._cfg(context, "general", "movement_timeout", 8.0))

        if elapsed > miss_timeout:
            pydirectinput.press("s")
            self._register_no_hp_failure(context, reason="moving_to_target_hp_timeout")
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            
            # Agressive Recovery: Kamera açısını değiştirip vizyona farklı taşlar sok
            self._log_ctx(context, "[MOVE] HP bar görünmedi, hedef ıskalandı veya sıkışıldı. Kamera döndürülüyor...")
            self._atomic_key_hold("q", 0.4)
            context.failed_click_count = 0
            context.last_clicked_pos = None
            
            self._set_state(context, BotState.SEARCHING)
            return 0.0

        if elapsed > movement_timeout:
            pydirectinput.press("s")
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            self._set_state(context, BotState.SEARCHING)
            self._log_ctx(context, "[MOVE] Movement timeout, returning SEARCH")
            return 0.0

        return 0.0

    def _execute_deferred_queue_clicks(self, context: BotContext) -> None:
        if not context.capturer or not context.vision:
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            return

        if not self._is_scene_stable(context):
            context.deferred_queue_click_at = time.time() + self.SCENE_STABILIZATION_SAMPLE_GAP_SEC
            self._log_ctx(context, "[QUEUE] Scene not stable yet, delaying queued clicks.")
            return

        self._log_ctx(context, f"[VERIFY] Bouncing to {context.deferred_queue_remaining} remaining queued targets...")
        fresh_frame = context.capturer.capture_frame()
        if fresh_frame is None:
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            return

        gx, gy, gw, gh = context.capturer.get_client_area_geometry()
        game_region = {"left": gx, "top": gy, "width": gw, "height": gh}

        # Need enough detections for the queue
        max_targets = context.deferred_queue_remaining + 1
        local_targets, detections = self._get_top_targets(context, fresh_frame, max_targets=max_targets)

        if not local_targets or len(local_targets) <= 1:
            self._log_ctx(context, "[VERIFY] No additional queue targets found.")
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            return

        context.multi_click_lock_active = True
        try:
            clicked_count = 0
            # Target 0 is likely what we're currently attacking, so we click the remaining
            for idx in range(1, len(local_targets)):
                step_target, step_detections = self._resolve_queue_step_target(
                    context=context,
                    base_targets=local_targets,
                    base_detections=detections,
                    max_targets=max_targets,
                    step_index=idx,
                )
                if step_target is not None:
                    target_anchor_x, target_anchor_y = int(step_target[0]), int(step_target[1])
                    resolved_click = self._resolve_safe_click_local(context, fresh_frame, step_target, step_detections)
                    if resolved_click is None:
                        continue
                    click_local_x, click_local_y = resolved_click

                    click_global_x, click_global_y = context.capturer.get_screen_position(click_local_x, click_local_y)

                    self._sleep_with_failsafe(random.uniform(self.QUEUE_INTER_CLICK_MIN_SEC, self.QUEUE_INTER_CLICK_MAX_SEC))
                    clicked = self._human_click(context, click_global_x, click_global_y)
                    if clicked:
                        clicked_count += 1
                        
                if clicked_count >= context.deferred_queue_remaining:
                    break
        finally:
            context.multi_click_lock_active = False

        self._log_ctx(context, f"[VERIFY] Bounced to {clicked_count} additional targets.")
        context.queued_targets_count = 1 + max(0, clicked_count)
        context.deferred_queue_remaining = 0
        context.deferred_queue_click_at = 0.0

    def _state_verify_attack(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> float:  # NOSONAR
        now = time.time()
        elapsed = now - context.state_started_at

        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)
        if hp_visible:
            self._set_state(context, BotState.COMBAT)
            context.hp_acquire_deadline = 0.0
            context.queue_wait_until = 0.0
            context.combat_started_at = now
            context.combat_hp_missing_count = 0
            context.last_strafe_at = now
            context.combat_last_z_at = now
            context.captcha_wait_until = now + 2.0
            self._log_ctx(context, "[VERIFY] Attack verified, entering COMBAT. Holding client for 2s for captcha.")

            # PHASE 2 QUEUE CLICKS
            if context.deferred_queue_remaining > 0:
                if context.deferred_queue_click_at <= 0.0:
                    context.deferred_queue_click_at = now + self._deferred_queue_click_delay_sec(context)
                if now >= context.deferred_queue_click_at:
                    self._execute_deferred_queue_clicks(context)

            # SKILL CHECK (göz ucuyla yetenek kontrolü)
            if context.skill_manager:
                self._log_ctx(context, "[SKILL] Göz ucuyla yetenek kontrolü yapılıyor...")
                # Fetch a fresh frame for the skill check to ensure highest accuracy
                fresh_frame = context.capturer.capture_frame() if context.capturer else None
                if fresh_frame is None:
                    fresh_frame = frame
                context.skill_manager.check_and_refresh(
                    frame=fresh_frame,
                    log_callback=lambda m: self._log_ctx(context, m), 
                    context="POST_VERIFY", 
                    fast_cast=True
                )

            return 0.0

        if context.hp_acquire_deadline > 0.0 and now < context.hp_acquire_deadline:
            return 0.0

        verify_timeout = min(3.0, float(self._cfg(context, "general", "verify_timeout", 3.0)))
        if elapsed > verify_timeout:
            self._register_no_hp_failure(context, reason="verify_attack_hp_timeout")
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            
            # Agressive Recovery: Kamera açısını değiştirip vizyona farklı taşlar sok
            self._log_ctx(context, "[VERIFY] Doğrulama zaman aşımı, yeni hedef aranıyor. Kamera döndürüldü.")
            self._atomic_key_hold("q", 0.4)
            context.failed_click_count = 0
            context.last_clicked_pos = None
            
            self._set_state(context, BotState.SEARCHING)

        return 0.0

    def _state_combat(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> float:  # NOSONAR
        now = time.time()
        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)

        if not hp_visible:
            context.combat_hp_missing_count += 1
            if context.combat_hp_missing_count >= 1:
                context.stones_destroyed += 1
                context.last_kill_at = now
                context.failed_click_count = 0
                context.last_clicked_pos = None

                if context.queued_targets_count > 1:
                    context.queued_targets_count -= 1
                    queue_wait_grace_sec = self._queue_wait_grace_sec(context)
                    context.queue_wait_until = now + queue_wait_grace_sec
                    context.deferred_queue_remaining = 0
                    context.deferred_queue_click_at = 0.0
                    context.post_kill_priority_until = 0.0
                    context.post_kill_reacquire_pending = False
                    self._set_state(context, BotState.QUEUE_WAIT)
                    self._log_ctx(
                        context,
                        (
                            "[COMBAT] Target destroyed. "
                            "Waiting queued handoff "
                            f"(remaining={context.queued_targets_count}, grace={queue_wait_grace_sec:.1f}s)"
                        ),
                    )
                    return 0.0

                if bool(self._cfg(context, "general", "auto_loot", True)):
                    pydirectinput.press("z", presses=6, interval=0.02)

                context.queued_targets_count = 0
                context.deferred_queue_remaining = 0
                context.deferred_queue_click_at = 0.0
                context.queue_wait_until = 0.0
                context.post_kill_priority_until = now + self.POST_KILL_PRIORITY_SEC
                context.post_kill_reacquire_pending = True
                self._set_state(context, BotState.SEARCHING)
                self._log_ctx(context, f"[COMBAT] Target destroyed. Total={context.stones_destroyed}")
            return 0.0

        context.combat_hp_missing_count = 0

        # Passive loot taps while target is alive.
        if bool(self._cfg(context, "general", "auto_loot", True)) and (now - context.combat_last_z_at >= 1.0):
            pydirectinput.press("z")
            context.combat_last_z_at = now

        # Anti-stuck strafing while in combat.
        anti_stuck = bool(self._cfg(context, "general", "anti_stuck", True))
        strafe_start_delay = float(self._cfg(context, "combat", "strafe_start_delay", 2.0))
        strafe_interval = float(self._cfg(context, "combat", "strafe_interval", 1.0))

        if anti_stuck and (now - context.combat_started_at >= strafe_start_delay):
            if now - context.last_strafe_at >= strafe_interval:
                key = context.combat_strafe_direction
                context.combat_strafe_direction = "d" if key == "a" else "a"
                self._atomic_key_hold(key, 0.2)
                context.last_strafe_at = now

        combat_timeout = float(self._cfg(context, "general", "combat_timeout", 120.0))
        if now - context.combat_started_at > combat_timeout:
            pydirectinput.press("s")
            context.queued_targets_count = 0
            context.queue_wait_until = 0.0
            self._set_state(context, BotState.SEARCHING)
            self._log_ctx(context, "[COMBAT] Timeout, reset to SEARCH")

        return 0.0

    def _state_queue_wait(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> None:
        now = time.time()
        hp_visible, _ = Vision.is_hp_bar_visible(frame, game_region=game_region, threshold=0.6)

        if hp_visible:
            self._set_state(context, BotState.COMBAT)
            context.queue_wait_until = 0.0
            context.combat_started_at = now
            context.combat_hp_missing_count = 0
            context.last_strafe_at = now
            context.combat_last_z_at = now
            self._log_ctx(context, f"[QUEUE] Next target engaged (remaining={context.queued_targets_count})")
            return

        if now >= context.queue_wait_until:
            context.queued_targets_count = 0
            context.deferred_queue_remaining = 0
            context.deferred_queue_click_at = 0.0
            context.queue_wait_until = 0.0
            self._set_state(context, BotState.SEARCHING)
            self._log_ctx(context, "[QUEUE] Grace expired without HP bar, returning SEARCH")

    def _resolve_queue_step_target(
        self,
        context: BotContext,
        base_targets: List[Tuple[int, int]],
        base_detections: List[Dict[str, Any]],
        max_targets: int,
        step_index: int,
    ) -> Tuple[Optional[Tuple[int, int]], List[Dict[str, Any]]]:
        """
        Resolve target for a queue step using a fresh frame after the first click.

        This prevents stale local coordinates when character movement shifts the camera.
        """
        if len(base_targets) <= step_index:
            return None, []

        selected = (int(base_targets[step_index][0]), int(base_targets[step_index][1]))

        if not context.capturer or not context.vision:
            return selected, base_detections

        refreshed_frame = context.capturer.capture_frame()
        if refreshed_frame is None:
            return selected, base_detections

        refreshed_targets, refreshed_detections = self._get_top_targets(
            context,
            refreshed_frame,
            max_targets=max_targets,
        )
        if len(refreshed_targets) <= step_index:
            return selected, base_detections

        refreshed = refreshed_targets[step_index]
        refreshed_xy = (int(refreshed[0]), int(refreshed[1]))
        if self._target_shift_px(selected, refreshed_xy) <= self.SNAP_ACTION_SHIFT_THRESHOLD_PX:
            return selected, base_detections

        self._log_ctx(
            context,
            (
                f"[SNAP] Queue target shift>{self.SNAP_ACTION_SHIFT_THRESHOLD_PX:.0f}px "
                f"({self._target_shift_px(selected, refreshed_xy):.1f}px), re-locking step target."
            ),
        )
        return refreshed_xy, refreshed_detections

    def _resolve_humanized_click_local(
        self,
        target_center: Tuple[int, int],
        detections: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """
        Select a random click point within the central 60% of the target bbox.
        Falls back to target center when bbox data is unavailable.
        """
        center_x, center_y = int(target_center[0]), int(target_center[1])

        if not detections:
            return center_x, center_y

        best_rect: Optional[List[float]] = None
        best_distance = float("inf")

        for det in detections:
            rect = det.get("rect", [])
            if not isinstance(rect, list) or len(rect) != 4:
                continue

            det_center = det.get("center")
            if isinstance(det_center, (tuple, list)) and len(det_center) == 2:
                det_cx, det_cy = float(det_center[0]), float(det_center[1])
            else:
                det_cx = (float(rect[0]) + float(rect[2])) * 0.5
                det_cy = (float(rect[1]) + float(rect[3])) * 0.5

            dist = ((det_cx - center_x) ** 2 + (det_cy - center_y) ** 2) ** 0.5
            if dist < best_distance:
                best_distance = dist
                best_rect = rect

        if best_rect is None:
            return center_x, center_y

        x1, y1, x2, y2 = [float(v) for v in best_rect]
        width = max(2.0, x2 - x1)
        height = max(2.0, y2 - y1)

        margin_x = width * 0.20
        margin_y = height * 0.20

        safe_left = x1 + margin_x
        safe_right = x2 - margin_x
        safe_top = y1 + margin_y
        safe_bottom = y2 - margin_y

        if safe_right <= safe_left:
            safe_left, safe_right = x1, x2
        if safe_bottom <= safe_top:
            safe_top, safe_bottom = y1, y2

        rand_x = int(random.uniform(safe_left, safe_right))
        rand_y = int(random.uniform(safe_top, safe_bottom))
        return rand_x, rand_y

    def _resolve_safe_click_local(
        self,
        context: BotContext,
        frame: np.ndarray,
        target_center: Tuple[int, int],
        detections: List[Dict[str, Any]],
    ) -> Optional[Tuple[int, int]]:
        if frame is None or not isinstance(frame, np.ndarray):
            return self._resolve_humanized_click_local(target_center, detections)

        frame_height, frame_width = frame.shape[:2]
        mask_regions = self._cfg(context, "vision", "mask_regions", [])
        if not isinstance(mask_regions, list) or not mask_regions:
            return self._resolve_humanized_click_local(target_center, detections)

        click_x, click_y = self._resolve_humanized_click_local(target_center, detections)
        if not point_in_mask_regions(click_x, click_y, mask_regions, frame_width, frame_height):
            return click_x, click_y

        center_x, center_y = int(target_center[0]), int(target_center[1])
        if not point_in_mask_regions(center_x, center_y, mask_regions, frame_width, frame_height):
            return center_x, center_y

        return None

    def _state_loot(self, context: BotContext) -> float:
        if bool(self._cfg(context, "general", "auto_loot", True)):
            pydirectinput.press("z", presses=8, interval=0.02)
        context.queued_targets_count = 0
        context.deferred_queue_remaining = 0
        context.deferred_queue_click_at = 0.0
        context.queue_wait_until = 0.0
        self._set_state(context, BotState.SEARCHING)
        return 0.0

    def _run_priority_skill_check(self, context: BotContext, frame: np.ndarray) -> bool:
        use_skills = bool(self._cfg(context, "skills", "use_skills", True))
        if not use_skills or not context.skill_manager:
            return False

        skill_interval = float(self._cfg(context, "skills", "check_interval", 7.0))
        now = time.time()
        if now - context.last_skill_check < skill_interval:
            return False

        context.last_skill_check = now
        roi = self._extract_top_left_roi(frame)
        if roi is None:
            return False

        refreshed = context.skill_manager.check_and_refresh_from_roi(
            roi,
            log_callback=lambda m, c=context: self._log_ctx(c, m),
            context="PRIORITY_SKILL",
            fast_cast=True,
        )
        if refreshed:
            self._flush_input_barrier(context, reason="priority_skill_refresh")

        return refreshed

    # ------------------------------------------------------------------
    # Non-critical periodic tasks
    # ------------------------------------------------------------------

    def _run_non_critical_tasks(self, context: BotContext, frame: np.ndarray, game_region: Dict[str, int]) -> float:  # NOSONAR
        if context.state != BotState.SEARCHING:
            return 0.0

        now = time.time()

        # Auto-equip remains optional and guarded by manager cooldown.
        if context.inventory_manager and context.inventory_manager.can_check() and self.driver:
            try:
                context.inventory_manager.driver = self.driver  # type: ignore[assignment]
                context.inventory_manager.capturer = context.capturer  # type: ignore[assignment]
                acted = context.inventory_manager.check_and_equip(
                    frame=frame,
                    game_region=game_region,
                    log_callback=lambda m, c=context: self._log_ctx(c, m),
                    capture_callback=(context.capturer.capture_frame if context.capturer else None),
                )
                if acted:
                    context.action_locked_until = time.time() + 0.4
                    return 0.4
            except Exception as equip_error:
                self._log_ctx(context, f"[EQUIP] [WARN] {equip_error}")

        # Quest cycle
        quest_enabled = bool(self._cfg(context, "quest", "enabled", False))
        quest_interval = float(self._cfg(context, "quest", "check_interval", 3.0))
        if quest_enabled and context.quest_handler and (now - context.last_quest_check >= quest_interval):
            context.last_quest_check = now
            try:
                if context.quest_handler.should_run(frame, game_region, log_callback=lambda m, c=context: self._log_ctx(c, m)):
                    context.quest_handler.driver = self.driver  # type: ignore[assignment]
                    context.quest_handler.capturer = context.capturer  # type: ignore[assignment]
                    success = context.quest_handler.perform_cycle(
                        frame=frame,
                        game_region=game_region,
                        log_callback=lambda m, c=context: self._log_ctx(c, m),
                        capture_callback=(context.capturer.capture_frame if context.capturer else None),
                    )
                    if success:
                        context.action_locked_until = time.time() + self.CRITICAL_ACTION_BUFFER_SEC
                        return self.CRITICAL_ACTION_BUFFER_SEC
            except Exception as quest_error:
                self._log_ctx(context, f"[QUEST] [WARN] {quest_error}")

        return 0.0

    # ------------------------------------------------------------------
    # Critical action handlers
    # ------------------------------------------------------------------

    def _handle_death_protocol(
        self,
        context: BotContext,
        game_region: Dict[str, int],
        dead_pos_local: Tuple[int, int],
    ) -> float:
        if not self.driver:
            return 0.0

        pydirectinput.keyUp("space")
        pydirectinput.keyUp("1")
        self._sleep_with_failsafe(1.5)

        template_center_x, template_center_y = dead_pos_local
        local_click_x = template_center_x
        local_click_y = template_center_y - 17

        if context.capturer:
            click_x, click_y = context.capturer.get_screen_position(local_click_x, local_click_y)
        else:
            click_x = game_region["left"] + local_click_x
            click_y = game_region["top"] + local_click_y

        self.driver.move_abs(click_x, click_y)
        self._sleep_with_failsafe(0.12)
        self.driver.click(duration_ms=25)

        self._sleep_with_failsafe(1.0)

        if context.mount_checker:
            try:
                is_unmounted = context.mount_checker.check_is_unmounted(
                    press_key=True,
                    game_region=game_region,
                )
                if is_unmounted:
                    self._atomic_ctrl_g(0.05)
                    self._sleep_with_failsafe(0.25)
            except Exception:
                pass

        return self.CRITICAL_ACTION_BUFFER_SEC

    async def _solve_captcha_once(  # NOSONAR
        self,
        context: BotContext,
        captcha_rect: Dict[str, int],
        game_region: Dict[str, int],
        frame: np.ndarray,
    ) -> bool:
        if not self.driver:
            return False

        api_key = str(self.global_captcha_cfg.get("api_key", "")).strip() or None
        model_name = str(self.global_captcha_cfg.get("selected_model", DEFAULT_GEMINI_MODEL)).strip() or DEFAULT_GEMINI_MODEL

        attempts = 0
        max_attempts = 5
        current_rect = dict(captcha_rect)

        try:
            while attempts < max_attempts:
                attempts += 1

                if self.stop_event and self.stop_event.is_set():
                    return False

                # Re-force window foreground before each retry capture.
                # This guards against focus being stolen between attempts.
                if self.is_global_captcha_panic:
                    self._force_captcha_window_foreground(context)

                working_frame = frame
                if context.capturer:
                    latest = context.capturer.capture_frame()
                    if latest is not None:
                        working_frame = latest

                found, fresh_rect = Vision.check_for_captcha(working_frame)
                if not found:
                    return True
                if fresh_rect:
                    current_rect = fresh_rect

                extraction_result = Vision.get_captcha_grid_image(current_rect, working_frame, temp_dir="temp")
                if extraction_result is None:
                    self._sleep_with_failsafe(0.2)
                    continue

                grid_image_path, grid_origin = extraction_result
                predicted_square = await asyncio.wait_for(
                    solve_captcha_with_gemini(
                        grid_image_path,
                        api_key=api_key,
                        model_name=model_name,
                    ),
                    timeout=8.5,
                )
                if predicted_square is None:
                    self._sleep_with_failsafe(0.2)
                    continue

                square = int(predicted_square)
                local_click = self._captcha_pick_local_click_point(
                    captcha_rect=current_rect,
                    image_index=square,
                    frame_shape=working_frame.shape,
                    grid_origin=grid_origin,
                )
                if local_click is None:
                    self._sleep_with_failsafe(0.2)
                    continue

                local_x, local_y = local_click
                if context.capturer:
                    click_x, click_y = context.capturer.get_screen_position(local_x, local_y)
                else:
                    click_x = int(game_region["left"] + local_x)
                    click_y = int(game_region["top"] + local_y)

                # Ensure focus is still on the CAPTCHA window before sending input.
                if self.is_global_captcha_panic:
                    self._force_captcha_window_foreground(context)

                if not self._human_click(context, click_x, click_y):
                    self._sleep_with_failsafe(0.2)
                    continue

                captcha_resolved = False
                button_clicked = False
                screenshot_after = None
                verify_deadline = time.time() + 1.25

                while time.time() < verify_deadline:
                    screenshot_after = context.capturer.capture_frame() if context.capturer else None
                    if screenshot_after is None:
                        self._sleep_with_failsafe(0.06)
                        continue

                    if not button_clicked:
                        found_button, button_rect = Vision.find_onayla_button(screenshot_after, threshold=0.62)
                        if found_button and button_rect:
                            btn_local_x = int(button_rect["center_x"])
                            btn_local_y = int(button_rect["center_y"])
                            if context.capturer:
                                global_x, global_y = context.capturer.get_screen_position(btn_local_x, btn_local_y)
                            else:
                                global_x = int(game_region["left"] + btn_local_x)
                                global_y = int(game_region["top"] + btn_local_y)
                            self._human_click(context, global_x, global_y)
                            button_clicked = True
                            self._sleep_with_failsafe(0.08)
                            continue

                    still_visible, _ = Vision.check_for_captcha(screenshot_after, threshold=0.66)
                    if not still_visible:
                        captcha_resolved = True
                        break

                    self._sleep_with_failsafe(0.07)

                if captcha_resolved:
                    return True
                else:
                    self._log_ctx(context, f"[CAPTCHA] Pencere kapanmadı (Deneme {attempts}/{max_attempts}). Yanlış çözüm veya süresi doldu, anında tekrar deneniyor.")

                if screenshot_after is None:
                    continue

            self._log_ctx(context, "[CAPTCHA] Max retries reached, stopping bot for safety.")
            if self.stop_event:
                self.stop_event.set()
            return False

        except asyncio.TimeoutError:
            self._log_ctx(context, "[CAPTCHA] [WARN] Gemini response timeout (8.5s)")
            return False
        except Exception as captcha_error:
            self._log_ctx(context, f"[CAPTCHA] [WARN] {captcha_error}")
            return False
        finally:
            try:
                temp_file = os.path.join("temp", "combined_captcha_grid.png")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _reset_context_session_timer(self, context: BotContext) -> None:
        context.next_break_threshold = random.uniform(self.SESSION_WORK_MIN_SEC, self.SESSION_WORK_MAX_SEC)
        context.session_start_time = time.time()

    def _reset_context_break_timer(self, context: BotContext) -> None:
        context.break_duration = random.uniform(self.SESSION_BREAK_MIN_SEC, self.SESSION_BREAK_MAX_SEC)
        context.break_started_at = time.time()
        context.last_break_status_at = 0.0
        context.last_break_minutes_left = -1

    def _emit_session_status(self, force: bool = False) -> None:
        if not self.status_callback:
            return

        now = time.time()
        active_breaks: List[str] = []
        for context in self.contexts:
            if not context.is_on_break:
                continue

            remaining = max(0.0, (context.break_started_at + context.break_duration) - now)
            minutes_left = max(1, int(math.ceil(remaining / 60.0)))
            tag = "C1" if context.slot == "client_1" else ("C2" if context.slot == "client_2" else context.slot)
            active_breaks.append(f"{tag} {minutes_left}m")

        if active_breaks:
            message = f"Status: ON BREAK ({', '.join(active_breaks)} left)"
        else:
            message = "Status: Running..."

        if not force and message == self._last_session_status_message:
            return

        self._last_session_status_message = message

        try:
            self.status_callback(message)
        except Exception:
            pass

    def _handle_context_session_break(self, context: BotContext, now: float) -> bool:
        if context.next_break_threshold <= 0.0:
            self._reset_context_session_timer(context)

        if context.is_on_break:
            break_end = context.break_started_at + context.break_duration
            remaining = break_end - now
            if remaining <= 0.0:
                context.is_on_break = False
                context.break_started_at = 0.0
                context.break_duration = 0.0
                context.last_break_status_at = 0.0
                context.last_break_minutes_left = -1
                self._reset_context_session_timer(context)
                self._log_ctx(context, "[SECURITY] Random break finished. Resuming work block.")
                self._emit_session_status(force=True)
                return False

            minutes_left = max(1, int(math.ceil(remaining / 60.0)))
            should_emit_status = (
                context.last_break_status_at <= 0.0
                or (now - context.last_break_status_at) >= self.SESSION_STATUS_EMIT_INTERVAL_SEC
                or minutes_left != context.last_break_minutes_left
            )
            if should_emit_status:
                context.last_break_status_at = now
                context.last_break_minutes_left = minutes_left
                self._emit_session_status()

            return True

        if context.captcha_active or context.state == BotState.SOLVING_CAPTCHA:
            return False

        elapsed = now - context.session_start_time
        if elapsed <= context.next_break_threshold:
            return False

        context.is_on_break = True
        self._reset_context_break_timer(context)
        context.queued_targets_count = 0
        context.deferred_queue_remaining = 0
        context.deferred_queue_click_at = 0.0
        context.queue_wait_until = 0.0
        context.hp_acquire_deadline = 0.0
        context.post_kill_priority_until = 0.0
        context.post_kill_reacquire_pending = False
        context.queued_stone_ids.clear()

        if context.state != BotState.SEARCHING:
            self._set_state(context, BotState.SEARCHING)

        if self.driver:
            try:
                self.driver.release_all_inputs()
            except Exception:
                pass

        self._log_ctx(context, "[SECURITY] Work block finished. Taking a random break...")
        self._emit_session_status(force=True)
        return True

    def _cfg(self, context: BotContext, section: str, key: str, default: Any) -> Any:
        return context.config.get(section, {}).get(key, default)

    def _reachable_distance_px(self, context: BotContext) -> float:
        raw_value = self._cfg(
            context,
            "combat",
            "reachable_distance_px",
            self.DEFAULT_REACHABLE_DISTANCE_PX,
        )
        try:
            threshold = float(raw_value)
        except Exception:
            threshold = self.DEFAULT_REACHABLE_DISTANCE_PX

        if threshold <= 0.0:
            return float("inf")
        return threshold

    def _deferred_queue_click_delay_sec(self, context: BotContext) -> float:
        raw_value = self._cfg(
            context,
            "combat",
            "deferred_queue_click_delay_sec",
            self.DEFAULT_DEFERRED_QUEUE_CLICK_DELAY_SEC,
        )
        try:
            delay_sec = float(raw_value)
        except Exception:
            delay_sec = self.DEFAULT_DEFERRED_QUEUE_CLICK_DELAY_SEC

        return max(0.0, delay_sec)

    def _batch_queue_stabilization_sec(self, context: BotContext) -> float:
        raw_value = self._cfg(
            context,
            "combat",
            "batch_queue_stabilization_sec",
            self.DEFAULT_BATCH_QUEUE_STABILIZATION_SEC,
        )
        try:
            delay_sec = float(raw_value)
        except Exception:
            delay_sec = self.DEFAULT_BATCH_QUEUE_STABILIZATION_SEC

        return max(0.0, delay_sec)

    def _queue_wait_grace_sec(self, context: BotContext) -> float:
        raw_value = self._cfg(
            context,
            "combat",
            "queue_wait_grace_sec",
            self.DEFAULT_QUEUE_WAIT_GRACE_SEC,
        )
        try:
            configured_grace_sec = float(raw_value)
        except Exception:
            configured_grace_sec = self.DEFAULT_QUEUE_WAIT_GRACE_SEC

        miss_timeout_raw = self._cfg(context, "general", "miss_timeout", 3.0)
        try:
            miss_timeout_sec = float(miss_timeout_raw)
        except Exception:
            miss_timeout_sec = 3.0

        adaptive_floor_sec = max(1.5, min(4.0, miss_timeout_sec + 0.8))
        bounded_config_sec = min(8.0, max(0.0, configured_grace_sec))
        return max(adaptive_floor_sec, bounded_config_sec)

    def _target_distance_px(
        self,
        target: Tuple[int, int],
        detections: List[Dict[str, Any]],
        frame: np.ndarray,
    ) -> float:
        target_x, target_y = int(target[0]), int(target[1])

        for det in detections:
            det_center = det.get("center")
            if not isinstance(det_center, (tuple, list)) or len(det_center) != 2:
                continue

            if int(det_center[0]) != target_x or int(det_center[1]) != target_y:
                continue

            raw_distance = det.get("distance")
            if raw_distance is None:
                break

            try:
                return float(raw_distance)
            except Exception:
                break

        frame_h, frame_w = frame.shape[:2]
        center_x = frame_w * 0.5
        center_y = frame_h * 0.5
        return math.hypot(float(target_x) - center_x, float(target_y) - center_y)

    def _run_post_hp_antistuck_burst(self, context: BotContext) -> None:
        """Run a short randomized WASD burst right after HP lock acquisition."""
        _ = context
        keys = ["w", "a", "s", "d"]
        random.shuffle(keys)

        remaining_total = float(self.POST_HP_ANTISTUCK_MAX_TOTAL_SEC)
        min_hold = float(self.POST_HP_ANTISTUCK_MIN_KEY_SEC)
        max_hold = max(min_hold, float(self.POST_HP_ANTISTUCK_MAX_KEY_SEC))
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

    def _recover_target_before_yield(self, context: BotContext, game_region: Dict[str, int]) -> bool:
        """
        Try to reacquire/click a Metin on the same client before yielding control.

        Returns True when a new target click is issued (state becomes MOVING_TO_TARGET).
        """
        if not context.capturer or not context.vision:
            return False

        for attempt in range(max(1, int(self.HP_TIMEOUT_RECOVERY_MAX_ATTEMPTS))):
            frame = context.capturer.capture_frame()
            if frame is None:
                continue

            self._state_searching(context, frame, game_region)
            if context.state == BotState.MOVING_TO_TARGET:
                return True

            if attempt < self.HP_TIMEOUT_RECOVERY_MAX_ATTEMPTS - 1:
                context.last_roam_at = 0.0
                self._atomic_key_hold("q", self.HP_TIMEOUT_RECOVERY_ROTATE_SEC)

        return False

    def _get_top_targets(
        self,
        context: BotContext,
        frame: np.ndarray,
        max_targets: int,
    ) -> Tuple[List[Tuple[int, int]], List[Dict[str, Any]]]:
        if not context.vision:
            return [], []

        mask_regions = self._cfg(context, "vision", "mask_regions", [])
        if not isinstance(mask_regions, list):
            mask_regions = []

        return context.vision.get_top_targets(
            frame,
            max_targets=max_targets,
            mask_regions=mask_regions,
        )

    def _set_state(self, context: BotContext, next_state: BotState) -> None:
        if context.state != next_state:
            self._log_ctx(context, f"[STATE] {context.state.value} -> {next_state.value}")
        context.state = next_state
        context.state_started_at = time.time()

    def _extract_top_left_roi(self, frame: np.ndarray) -> Optional[np.ndarray]:
        if frame is None:
            return None

        if not isinstance(frame, np.ndarray):
            return None

        frame_np: np.ndarray = frame

        h, w = frame_np.shape[:2]
        x, y, rw, rh = calculate_relative_rect(w, h, 0.0, 0.0, 0.20, 0.15)
        roi = frame_np[y:y + rh, x:x + rw]
        return roi if roi.size > 0 else None

    def _to_overlay_detections(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        overlay_items: List[Dict[str, Any]] = []
        for det in detections:
            rect = det.get("rect", [])
            if not isinstance(rect, list) or len(rect) != 4:
                continue
            overlay_items.append(
                {
                    "rect": [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])],
                    "label": str(det.get("label", "metin")),
                    "conf": float(det.get("conf", 0.0)),
                }
            )
        return overlay_items

    def _atomic_key_hold(self, key: str, hold_duration: float) -> None:
        pydirectinput.keyDown(key)
        try:
            self._sleep_with_failsafe(hold_duration)
        finally:
            try:
                pydirectinput.keyUp(key)
            except Exception:
                pass

    def _atomic_ctrl_g(self, hold_duration: float = 0.05) -> None:
        pydirectinput.keyDown("ctrl")
        try:
            self._sleep_with_failsafe(hold_duration)
            pydirectinput.press("g")
        finally:
            try:
                pydirectinput.keyUp("ctrl")
            except Exception:
                pass

    def _human_click(self, context: BotContext, x: int, y: int, assume_mutex_held: bool = False) -> bool:
        if not self.driver:
            return False

        if context.capturer and not context.capturer.is_window_focused():
            return False

        owns_lock = False
        if not assume_mutex_held:
            if not self._action_mutex.acquire(blocking=False):
                return False
            owns_lock = True

        try:
            self.driver.move_abs(int(x), int(y))
            self._sleep_with_failsafe(random.uniform(self.CLICK_TRAVEL_MIN_SEC, self.CLICK_TRAVEL_MAX_SEC))
            if context.capturer and not context.capturer.is_window_focused():
                return False
            self.driver.click(duration_ms=random.randint(self.CLICK_DOWN_UP_MIN_MS, self.CLICK_DOWN_UP_MAX_MS))
            return True
        except Exception as click_error:
            self._log_ctx(context, f"[CLICK] [WARN] {click_error}")
            return False
        finally:
            if owns_lock:
                try:
                    self._action_mutex.release()
                except Exception:
                    pass

    def _flush_input_barrier(self, context: BotContext, reason: str) -> None:
        if not self.driver:
            return

        try:
            self.driver.release_all_inputs()
        except Exception as flush_error:
            self._log_ctx(context, f"[INPUT] [WARN] release_all_inputs failed ({reason}): {flush_error}")

    def _is_repeated_target(
        self,
        context: BotContext,
        x: int,
        y: int,
        game_region: Dict[str, int],
    ) -> bool:
        if not context.last_clicked_pos:
            return False

        last_x, last_y = context.last_clicked_pos
        dist = ((x - last_x) ** 2 + (y - last_y) ** 2) ** 0.5
        min_move = min(game_region.get("width", 1024), game_region.get("height", 768)) * 0.04

        if dist < min_move:
            context.failed_click_count += 1
            return True

        context.failed_click_count = 0
        return False

    def _register_no_hp_failure(self, context: BotContext, reason: str) -> None:
        context.no_hp_click_failures += 1
        if context.no_hp_click_failures < self.HP_RECALIBRATION_THRESHOLD:
            return

        now = time.time()
        if now - context.last_recalibration_attempt < self.MAX_RECALIBRATION_COOLDOWN_SEC:
            return

        context.last_recalibration_attempt = now
        context.no_hp_click_failures = 0

        if not self.driver:
            return

        try:
            self._log_ctx(context, f"[MOUSE] Recalibration triggered ({reason})")
            self.driver.reconnect(reason=reason)
        except Exception as recal_error:
            self._log_ctx(context, f"[MOUSE] [WARN] Recalibration failed: {recal_error}")

    def _mark_combat_yield(self, context: BotContext) -> None:
        now = time.time()

        if (
            self._last_combat_yield_slot
            and self._last_combat_yield_slot != context.slot
            and (now - self._last_combat_yield_at) <= self.GHOST_IDLE_CHAIN_WINDOW_SEC
        ):
            self._ghost_yield_pending = True

        self._last_combat_yield_slot = context.slot
        self._last_combat_yield_at = now

    def _clear_combat_yield_candidate(self, context: BotContext) -> None:
        if self._last_combat_yield_slot == context.slot:
            self._last_combat_yield_slot = None
            self._last_combat_yield_at = 0.0

        # Any non-combat action breaks the back-to-back ghost-yield chain.
        self._ghost_yield_pending = False

    def _consume_ghost_yield_delay(self) -> float:
        if not self._ghost_yield_pending:
            return 0.0

        self._ghost_yield_pending = False
        self._last_combat_yield_slot = None
        self._last_combat_yield_at = 0.0
        return random.uniform(self.GHOST_IDLE_SLEEP_MIN_SEC, self.GHOST_IDLE_SLEEP_MAX_SEC)

    def _emit_overlay(self, detections: list) -> None:
        if self.overlay_callback:
            try:
                self.overlay_callback(detections)
            except Exception:
                pass

    def _set_overlay_hidden(self, hidden: bool) -> None:
        """Hide or show the overlay window to prevent it from polluting MSS captures."""
        if self.overlay_hide_callback:
            try:
                self.overlay_hide_callback(hidden)
            except Exception:
                pass

    def _emit_stats(self) -> None:
        if not self.contexts:
            return

        self._emit_session_status()

        if not self.stats_callback:
            return

        total_targets = sum(ctx.stones_destroyed for ctx in self.contexts)
        start_time = min(ctx.bot_started_at for ctx in self.contexts)
        elapsed = max(0.0, time.time() - start_time)

        try:
            self.stats_callback(total_targets, elapsed)
        except Exception:
            pass

    def _cleanup(self) -> None:
        self._emit_session_status(force=True)

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
            self.driver = None

        for context in self.contexts:
            try:
                if context.capturer:
                    context.capturer.release()
            except Exception:
                pass

    def _sleep_with_failsafe(self, duration: float) -> None:
        if duration <= 0:
            return

        deadline = time.time() + duration
        while time.time() < deadline:
            if self.stop_event and self.stop_event.is_set():
                return
            if keyboard.is_pressed("F12"):
                if self.stop_event:
                    self.stop_event.set()
                return
            time.sleep(0.01)

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def _log_ctx(self, context: BotContext, message: str) -> None:
        tag = "C1" if context.slot == "client_1" else "C2"
        self._log(f"[{tag}] {message}")
