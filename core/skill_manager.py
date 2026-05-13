"""
Profile-based reactive skill manager.

Supports multiple classes/profiles (warrior, shaman, etc.) by loading
icon templates from assets/skills/<profile>/ and binding them to skill slots.
"""

from __future__ import annotations

import copy
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from core.dpi_utils import calculate_relative_rect


DEFAULT_SLOT_ORDER = ["skill_1", "skill_2", "skill_3", "buff_1", "buff_2"]
SLOT_NAME_PATTERN = re.compile(r"^(?:skill|buff|slot)_\d+$", re.IGNORECASE)

DEFAULT_PROFILE_BINDINGS: Dict[str, Dict[str, str]] = {
    "savasci_bedensel": {
        "skill_1": "aura.png",
        "skill_2": "berserk.png",
    },
    "saman_ejderha": {
        "skill_1": "ejderha_yardimi.png",
        "skill_2": "kutsama.png",
        "skill_3": "yansitma.png",
    },
    "saman_iyilestirme": {
        "skill_1": "hiz.png",
        "skill_2": "yuksek_saldiri.png",
    },
}


class SkillManager:
    def __init__(self, assets_dir: str = "assets/skills"):
        self.assets_dir = assets_dir

        # Buff/status icons are rendered on top-left side of active client.
        # Relative ROI keeps detection resolution-independent.
        self.roi_ratio = {"x": 0.0, "y": 0.0, "w": 0.20, "h": 0.15}
        self.match_threshold = 0.82
        self.missing_confirmations = 2

        self.active_profile = "savasci_bedensel"
        self.profile_bindings: Dict[str, Dict[str, str]] = copy.deepcopy(DEFAULT_PROFILE_BINDINGS)
        self.slot_order: List[str] = list(DEFAULT_SLOT_ORDER)

        self.slot_configs: Dict[str, Dict[str, object]] = {
            "skill_1": {"key": "4", "cooldown": 60.0, "enabled": True},
            "skill_2": {"key": "f2", "cooldown": 180.0, "enabled": True},
            "skill_3": {"key": "3", "cooldown": 20.0, "enabled": False},
            "buff_1": {"key": "f3", "cooldown": 200.0, "enabled": False},
            "buff_2": {"key": "f4", "cooldown": 200.0, "enabled": False},
        }

        self.templates_by_slot: Dict[str, np.ndarray] = {}
        self.last_slot_status: Dict[str, Optional[bool]] = dict.fromkeys(self.slot_order, None)
        self.missing_streak_by_slot: Dict[str, int] = dict.fromkeys(self.slot_order, 0)
        self.last_match_score_by_slot: Dict[str, float] = dict.fromkeys(self.slot_order, 0.0)
        self.total_casts_by_slot: Dict[str, int] = dict.fromkeys(self.slot_order, 0)

        self.is_mounted = True

        self.load_profile_templates(self.active_profile)

    def _is_slot_name(self, slot_name: str) -> bool:
        if not isinstance(slot_name, str):
            return False
        return bool(SLOT_NAME_PATTERN.match(slot_name.strip().lower()))

    def _slot_sort_key(self, slot_name: str) -> Tuple[int, int, str]:
        key = str(slot_name).strip().lower()
        parts = key.split("_", 1)

        prefix_order = {
            "skill": 0,
            "buff": 1,
            "slot": 2,
        }

        prefix = parts[0] if parts else "slot"
        order = prefix_order.get(prefix, 3)

        index = 9999
        if len(parts) == 2:
            try:
                index = int(parts[1])
            except ValueError:
                index = 9999

        return (order, index, key)

    def _sorted_slots(self, slots: List[str]) -> List[str]:
        unique = []
        seen = set()
        for slot in slots:
            slot_name = str(slot).strip().lower()
            if not self._is_slot_name(slot_name):
                continue
            if slot_name in seen:
                continue
            seen.add(slot_name)
            unique.append(slot_name)
        return sorted(unique, key=self._slot_sort_key)

    def _refresh_runtime_slot_order(self, candidate_slots: List[str]) -> None:
        merged = list(self.slot_order)
        merged.extend(candidate_slots)

        ordered = self._sorted_slots(merged)
        if not ordered:
            ordered = list(DEFAULT_SLOT_ORDER)

        self.slot_order = ordered

        for slot in self.slot_order:
            if slot not in self.slot_configs:
                self.slot_configs[slot] = {
                    "key": "",
                    "cooldown": 60.0,
                    "enabled": False,
                }

            self.last_slot_status.setdefault(slot, None)
            self.missing_streak_by_slot.setdefault(slot, 0)
            self.last_match_score_by_slot.setdefault(slot, 0.0)
            self.total_casts_by_slot.setdefault(slot, 0)

    def _collect_slots_from_bindings(self, bindings: Dict[str, Dict[str, str]]) -> List[str]:
        slots: List[str] = []
        if not isinstance(bindings, dict):
            return slots

        for profile_map in bindings.values():
            if not isinstance(profile_map, dict):
                continue
            for slot_name in profile_map.keys():
                if self._is_slot_name(str(slot_name).strip().lower()):
                    slots.append(str(slot_name).strip().lower())

        return slots

    # ------------------------------------------------------------------
    # Profile / config setup
    # ------------------------------------------------------------------

    def available_profiles(self) -> List[str]:
        discovered: List[str] = []
        if os.path.isdir(self.assets_dir):
            for item in sorted(os.listdir(self.assets_dir)):
                full_path = os.path.join(self.assets_dir, item)
                if os.path.isdir(full_path):
                    discovered.append(item)

        if not discovered:
            return sorted(DEFAULT_PROFILE_BINDINGS.keys())
        return discovered

    def configure(self, skills_config: Dict, log_callback=None) -> None:
        """
        Configure manager from config.skills section.
        """
        if not isinstance(skills_config, dict):
            return

        custom_bindings = skills_config.get("profile_bindings", {})
        self.profile_bindings = self._merge_profile_bindings(custom_bindings)

        dynamic_slots = list(self.slot_configs.keys())
        dynamic_slots.extend(self._collect_slots_from_bindings(self.profile_bindings))

        for key, value in skills_config.items():
            slot_name = str(key).strip().lower()
            if self._is_slot_name(slot_name) and isinstance(value, dict):
                dynamic_slots.append(slot_name)

        self._refresh_runtime_slot_order(dynamic_slots)

        for slot in self.slot_order:
            incoming = skills_config.get(slot, {})
            if not isinstance(incoming, dict):
                incoming = {}

            existing = self.slot_configs.get(slot, {})
            key = str(incoming.get("key", existing.get("key", ""))).strip().lower()

            cooldown_raw = incoming.get("cooldown", existing.get("cooldown", 60.0))
            existing_cooldown = self._parse_float(existing.get("cooldown", 60.0), fallback=60.0, minimum=1.0)
            cooldown = self._parse_float(cooldown_raw, fallback=existing_cooldown, minimum=1.0)

            enabled_default = bool(existing.get("enabled", False))
            enabled = bool(incoming.get("enabled", enabled_default))

            self.slot_configs[slot] = {
                "key": key,
                "cooldown": cooldown,
                "enabled": enabled,
            }

        requested_profile = str(skills_config.get("active_profile", self.active_profile)).strip()
        if requested_profile:
            self.active_profile = requested_profile

        self.load_profile_templates(self.active_profile, log_callback=log_callback)

    def _merge_profile_bindings(self, custom_bindings: Dict) -> Dict[str, Dict[str, str]]:
        merged = copy.deepcopy(DEFAULT_PROFILE_BINDINGS)

        if not isinstance(custom_bindings, dict):
            return merged

        for profile_name, binding_map in custom_bindings.items():
            if not isinstance(profile_name, str) or not isinstance(binding_map, dict):
                continue

            profile = merged.setdefault(profile_name, {})
            for slot_name, filename in binding_map.items():
                slot = str(slot_name).strip().lower()
                if not self._is_slot_name(slot):
                    continue
                if not isinstance(filename, str) or not filename.strip():
                    continue
                profile[slot] = filename.strip()

        return merged

    def _parse_float(self, raw_value, fallback: float, minimum: float) -> float:
        if isinstance(raw_value, (int, float)):
            return max(minimum, float(raw_value))

        if isinstance(raw_value, str):
            try:
                return max(minimum, float(raw_value.strip()))
            except ValueError:
                return max(minimum, fallback)

        return max(minimum, fallback)

    def _reset_template_runtime_state(self) -> None:
        self.templates_by_slot = {}

        for slot in self.slot_order:
            self.last_slot_status[slot] = None
            self.missing_streak_by_slot[slot] = 0
            self.last_match_score_by_slot[slot] = 0.0

    def _list_profile_png_files(self, profile_path: str) -> List[str]:
        if not os.path.isdir(profile_path):
            return []

        png_files: List[str] = []
        for item in sorted(os.listdir(profile_path)):
            full_path = os.path.join(profile_path, item)
            if not os.path.isfile(full_path):
                continue
            if not item.lower().endswith(".png"):
                continue
            png_files.append(item)

        return png_files

    def load_profile_templates(self, profile_name: str, log_callback=None) -> None:
        requested_profile = str(profile_name).strip()
        if requested_profile:
            self.active_profile = requested_profile

        profiles = self.available_profiles()
        if self.active_profile not in profiles and profiles:
            self.active_profile = profiles[0]

        profile_path = os.path.join(self.assets_dir, self.active_profile)
        existing_bindings = dict(self.profile_bindings.get(self.active_profile, {}))
        profile_bindings: Dict[str, str] = {}

        profile_png_files = self._list_profile_png_files(profile_path)
        profile_png_set = set(profile_png_files)

        if profile_png_set:
            for slot_name, filename in existing_bindings.items():
                slot = str(slot_name).strip().lower()
                candidate = str(filename).strip()

                if not self._is_slot_name(slot):
                    continue
                if not candidate.lower().endswith(".png"):
                    continue
                if candidate not in profile_png_set:
                    continue
                if candidate in profile_bindings.values():
                    continue

                profile_bindings[slot] = candidate

            self._auto_map_profile_files(profile_path, profile_bindings, extensions=(".png",))

        self.profile_bindings[self.active_profile] = dict(profile_bindings)
        self._refresh_runtime_slot_order(list(profile_bindings.keys()))
        self._reset_template_runtime_state()

        loaded_slots: List[str] = []
        for slot in self.slot_order:
            filename = profile_bindings.get(slot)
            if not filename:
                continue

            template_path = os.path.join(profile_path, filename)
            if not os.path.exists(template_path):
                continue

            template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if template is None:
                continue

            self.templates_by_slot[slot] = template
            loaded_slots.append(slot)

        self._log_profile_load(log_callback, loaded_slots)

    def _load_templates_for_active_profile(self, log_callback=None) -> None:
        # Backward-compatible wrapper used by legacy callers.
        self.load_profile_templates(self.active_profile, log_callback=log_callback)

    def _resolve_template(self, profile_path: str, filename: str):
        candidate_paths = [
            os.path.join(profile_path, filename),
            os.path.join(self.assets_dir, filename),
        ]
        for candidate in candidate_paths:
            if not os.path.exists(candidate):
                continue
            template = cv2.imread(candidate, cv2.IMREAD_COLOR)
            if template is not None:
                return template
        return None

    def _log_profile_load(self, log_callback, loaded_slots: List[str]) -> None:
        if not log_callback:
            return
        loaded_count = len(loaded_slots)
        enabled_count = sum(1 for slot in self.slot_order if bool(self.slot_configs.get(slot, {}).get("enabled", False)))
        log_callback(
            f"[SKILL] Profile '{self.active_profile}' loaded: {loaded_count} template(s), {enabled_count} active slot(s)."
        )

    def _auto_map_profile_files(
        self,
        profile_path: str,
        profile_bindings: Dict[str, str],
        extensions: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp"),
    ) -> None:
        files = [
            item for item in sorted(os.listdir(profile_path))
            if os.path.isfile(os.path.join(profile_path, item)) and item.lower().endswith(extensions)
        ]

        used_files = set(profile_bindings.values())
        free_files = [f for f in files if f not in used_files]

        next_skill_index = 1
        skill_indexes = []
        for slot_name in profile_bindings.keys():
            key = str(slot_name).strip().lower()
            if key.startswith("skill_"):
                try:
                    skill_indexes.append(int(key.split("_", 1)[1]))
                except ValueError:
                    continue
        if skill_indexes:
            next_skill_index = max(skill_indexes) + 1

        for filename in free_files:
            while f"skill_{next_skill_index}" in profile_bindings:
                next_skill_index += 1

            profile_bindings[f"skill_{next_skill_index}"] = filename
            next_skill_index += 1

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _get_roi_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        if frame is None:
            return None

        frame_h, frame_w = frame.shape[:2]
        x, y, w, h = calculate_relative_rect(
            frame_w,
            frame_h,
            self.roi_ratio["x"],
            self.roi_ratio["y"],
            self.roi_ratio["w"],
            self.roi_ratio["h"],
        )

        roi = frame[y:y + h, x:x + w]
        return roi if roi.size > 0 else None

    def extract_skill_roi(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Return top-left skill ROI for orchestrator-controlled visual checks."""
        return self._get_roi_frame(frame)

    def _find_template(self, frame: np.ndarray, template: np.ndarray) -> bool:
        return self._find_template_score(frame, template) >= self.match_threshold

    def _find_template_score(self, frame: np.ndarray, template: np.ndarray) -> float:
        try:
            if frame.shape[0] < template.shape[0] or frame.shape[1] < template.shape[1]:
                return 0.0

            if len(frame.shape) == 3:
                frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                frame_gray = frame

            if len(template.shape) == 3:
                template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            else:
                template_gray = template

            result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, _max_loc = cv2.minMaxLoc(result)
            return float(max_val)
        except Exception:
            return 0.0

    def check_slot_active(self, frame: np.ndarray, slot: str) -> bool:
        if slot not in self.slot_order:
            return True
        if not bool(self.slot_configs.get(slot, {}).get("enabled", False)):
            return True

        template = self.templates_by_slot.get(slot)
        if template is None:
            return True

        roi = self._get_roi_frame(frame)
        if roi is None:
            return True

        return self._find_template(roi, template)

    def _evaluate_slot_activity(self, roi: np.ndarray, slot: str) -> Optional[Tuple[bool, float]]:
        if not bool(self.slot_configs.get(slot, {}).get("enabled", False)):
            return None

        template = self.templates_by_slot.get(slot)
        if template is None:
            return None

        score = self._find_template_score(roi, template)
        return (score >= self.match_threshold, score)

    def _update_slot_tracking(self, slot: str, active: bool, score: float, log_callback=None) -> None:
        self.last_match_score_by_slot[slot] = score

        if active:
            self.missing_streak_by_slot[slot] = 0
        else:
            self.missing_streak_by_slot[slot] = self.missing_streak_by_slot.get(slot, 0) + 1

        previous = self.last_slot_status.get(slot)
        self.last_slot_status[slot] = active

        if previous is not None and previous != active and log_callback:
            state = "ACTIVE" if active else "MISSING"
            log_callback(f"[SKILL] {slot} status: {state} (score={score:.2f})")

    def _is_confirmed_missing(self, slot: str, active: bool) -> bool:
        if active:
            return False
        return self.missing_streak_by_slot.get(slot, 0) >= self.missing_confirmations

    def get_missing_slots(self, frame: np.ndarray, log_callback=None) -> List[str]:
        missing: List[str] = []
        roi = self._get_roi_frame(frame)
        if roi is None:
            return missing

        return self.get_missing_slots_from_roi(roi, log_callback=log_callback)

    def get_missing_slots_from_roi(self, roi: np.ndarray, log_callback=None) -> List[str]:
        missing: List[str] = []
        if roi is None or roi.size == 0:
            return missing

        for slot in self.slot_order:
            slot_result = self._evaluate_slot_activity(roi, slot)
            if slot_result is None:
                continue

            active, score = slot_result
            self._update_slot_tracking(slot, active, score, log_callback)

            if self._is_confirmed_missing(slot, active):
                missing.append(slot)

        return missing

    # ------------------------------------------------------------------
    # Casting helpers
    # ------------------------------------------------------------------

    def _dismount(self, fast: bool = False) -> None:
        import pydirectinput

        pydirectinput.keyDown("ctrl")
        time.sleep(0.05)
        pydirectinput.press("g")
        pydirectinput.keyUp("ctrl")
        time.sleep(0.15 if fast else 0.4)
        self.is_mounted = False

    def _remount(self, fast: bool = False) -> None:
        import pydirectinput

        pydirectinput.keyDown("ctrl")
        time.sleep(0.05)
        pydirectinput.press("g")
        pydirectinput.keyUp("ctrl")
        time.sleep(0.15 if fast else 0.3)
        self.is_mounted = True

    def _collect_castable_slots(self, slots: List[str]) -> List[str]:
        ordered_slots = [slot for slot in self.slot_order if slot in slots]
        castable: List[str] = []

        for slot in ordered_slots:
            cfg = self.slot_configs.get(slot, {})
            key = str(cfg.get("key", "")).strip().lower()
            enabled = bool(cfg.get("enabled", False))

            if not enabled or not key:
                continue
            castable.append(slot)

        return castable

    def _cast_single_slot(self, slot: str, fast_cast: bool, log_callback=None) -> bool:
        import pydirectinput

        key = str(self.slot_configs.get(slot, {}).get("key", "")).strip().lower()
        if not key:
            return False

        pydirectinput.press(key)
        time.sleep(0.15 if fast_cast else 0.6)

        self.total_casts_by_slot[slot] = self.total_casts_by_slot.get(slot, 0) + 1

        if log_callback:
            log_callback(f"[SKILL] {slot} refreshed with key '{key}'.")
        return True

    def cast_slots(self, slots: List[str], log_callback=None, fast_cast: bool = True) -> List[str]:
        if not slots:
            return []

        castable = self._collect_castable_slots(slots)

        if not castable:
            return []

        casted: List[str] = []
        for slot in castable:
            # Deterministic sequence per skill:
            # always dismount -> cast one slot -> remount.
            if log_callback:
                log_callback(f"[SKILL] {slot} refresh sequence: dismount -> cast -> remount")

            self._dismount(fast=fast_cast)
            cast_ok = self._cast_single_slot(slot, fast_cast, log_callback=log_callback)
            self._remount(fast=fast_cast)
            time.sleep(0.2)

            if cast_ok:
                casted.append(slot)

        return casted

    def check_and_refresh(self, frame: np.ndarray, log_callback=None, context: str = "SKILL", fast_cast: bool = True) -> bool:
        missing = self.get_missing_slots(frame, log_callback=log_callback)
        if not missing:
            return False

        if log_callback:
            joined = ", ".join(missing)
            log_callback(f"[{context}] Missing slot(s): {joined}. Refreshing...")

        casted = self.cast_slots(missing, log_callback=log_callback, fast_cast=fast_cast)
        return len(casted) > 0

    def check_and_refresh_from_roi(self, roi: np.ndarray, log_callback=None, context: str = "SKILL", fast_cast: bool = True) -> bool:
        missing = self.get_missing_slots_from_roi(roi, log_callback=log_callback)
        if not missing:
            return False

        if log_callback:
            joined = ", ".join(missing)
            log_callback(f"[{context}] Missing slot(s): {joined}. Refreshing...")

        casted = self.cast_slots(missing, log_callback=log_callback, fast_cast=fast_cast)
        return len(casted) > 0

    def check_and_refresh_in_combat(self, frame: np.ndarray, log_callback=None) -> bool:
        return self.check_and_refresh(frame, log_callback=log_callback, context="COMBAT BUFF", fast_cast=True)

    # ------------------------------------------------------------------
    # Legacy compatibility wrappers
    # ------------------------------------------------------------------

    def check_aura(self, frame: np.ndarray) -> bool:
        return self.check_slot_active(frame, "skill_1")

    def check_berserk(self, frame: np.ndarray) -> bool:
        return self.check_slot_active(frame, "skill_2")

    def cast_aura(self, log_callback=None, force_dismount=True, fast_cast=False) -> bool:
        _ = force_dismount
        return len(self.cast_slots(["skill_1"], log_callback=log_callback, fast_cast=fast_cast)) > 0

    def cast_berserk(self, log_callback=None, force_dismount=True, fast_cast=False) -> bool:
        _ = force_dismount
        return len(self.cast_slots(["skill_2"], log_callback=log_callback, fast_cast=fast_cast)) > 0

    def cast_both_skills(self, log_callback=None, fast_cast=False) -> bool:
        return len(self.cast_slots(["skill_1", "skill_2"], log_callback=log_callback, fast_cast=fast_cast)) > 0

    def check_all_buffs(self, frame: np.ndarray) -> Dict[str, bool]:
        status = {
            slot: self.check_slot_active(frame, slot)
            for slot in self.slot_order
            if bool(self.slot_configs.get(slot, {}).get("enabled", False))
        }
        status["aura"] = status.get("skill_1", True)
        status["berserk"] = status.get("skill_2", True)
        return status

    def can_refresh(self) -> bool:
        for slot in self.slot_order:
            cfg = self.slot_configs.get(slot, {})
            enabled = bool(cfg.get("enabled", False))
            key = str(cfg.get("key", "")).strip().lower()
            has_template = self.templates_by_slot.get(slot) is not None

            if not enabled or not key or not has_template:
                continue

            return True

        return False

    def refresh_buffs(self, log_callback=None) -> bool:
        return self.cast_both_skills(log_callback=log_callback, fast_cast=True)

    def get_stats(self) -> Dict[str, object]:
        return {
            "active_profile": self.active_profile,
            "templates_loaded": len(self.templates_by_slot),
            "slot_templates": sorted(self.templates_by_slot.keys()),
            "slot_casts": dict(self.total_casts_by_slot),
            "is_mounted": self.is_mounted,
        }
