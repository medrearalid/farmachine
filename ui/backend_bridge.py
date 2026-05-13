"""
Backend bridge for dual-client QML UI.

Exposes runtime state and client-scoped config values via QProperties.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from core.ai.gemini_client import DEFAULT_GEMINI_MODEL, get_available_models


class BotSignals(QObject):
    """Thread-safe signals for bot <-> UI communication."""

    log_message = Signal(str)
    update_stat = Signal(str, str)
    update_overlay = Signal(list)
    overlay_hide = Signal(bool)
    highlight_window = Signal(int, str)
    bot_stopped = Signal()


class BackendBridge(QObject):
    # Runtime notify signals
    isRunningChanged = Signal()
    isAttachedChanged = Signal()
    attachedWindowNameChanged = Signal()
    statusTextChanged = Signal()
    destroyedCountChanged = Signal()
    elapsedTimeChanged = Signal()
    logTextChanged = Signal()
    windowListChanged = Signal()

    # Slot-specific runtime notify
    slot1AttachedChanged = Signal()
    slot2AttachedChanged = Signal()
    slot1WindowNameChanged = Signal()
    slot2WindowNameChanged = Signal()

    # Active client selector
    activeConfigClientChanged = Signal()

    # Config notify signals
    yoloConfidenceChanged = Signal()
    maskRegionCountChanged = Signal()
    selectionModeIndexChanged = Signal()
    combatTimeoutChanged = Signal()
    scanRadiusChanged = Signal()
    reviveDelayChanged = Signal()
    skillCheckIntervalChanged = Signal()
    activeSkillProfileChanged = Signal()
    availableSkillProfilesChanged = Signal()
    skillEntriesChanged = Signal()
    questCheckIntervalChanged = Signal()
    missTimeoutChanged = Signal()
    movementTimeoutChanged = Signal()
    verifyTimeoutChanged = Signal()
    strafeStartDelayChanged = Signal()
    strafeIntervalChanged = Signal()
    multiTargetQueueEnabledChanged = Signal()
    multiTargetQueueSizeChanged = Signal()
    deferredQueueClickDelayChanged = Signal()
    autoLootChanged = Signal()
    autoSkillsChanged = Signal()
    autoReviveChanged = Signal()
    antiStuckChanged = Signal()
    captchaSolverChanged = Signal()
    captchaEnabledChanged = Signal()
    captchaApiKeyChanged = Signal()
    captchaSelectedModelChanged = Signal()
    questEnabledChanged = Signal()
    debugModeChanged = Signal()
    mouseIdChanged = Signal()
    alwaysOnTopChanged = Signal()

    # Skill slot signals
    skill1KeyChanged = Signal()
    skill1CooldownChanged = Signal()
    skill1EnabledChanged = Signal()
    skill2KeyChanged = Signal()
    skill2CooldownChanged = Signal()
    skill2EnabledChanged = Signal()
    skill3KeyChanged = Signal()
    skill3CooldownChanged = Signal()
    skill3EnabledChanged = Signal()
    buff1KeyChanged = Signal()
    buff1CooldownChanged = Signal()
    buff1EnabledChanged = Signal()
    buff2KeyChanged = Signal()
    buff2CooldownChanged = Signal()
    buff2EnabledChanged = Signal()

    CLIENT_KEYS = ("client_1", "client_2")
    _IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    _SLOT_NAME_PATTERN = re.compile(r"^(?:skill|buff|slot)_\d+$", re.IGNORECASE)

    def _ensure_global_captcha_defaults(self, config_dict: dict) -> dict:
        global_cfg = config_dict.setdefault("global", {})
        captcha_cfg = global_cfg.get("captcha", {})
        if not isinstance(captcha_cfg, dict):
            captcha_cfg = {}
        captcha_cfg.setdefault("enabled", True)
        captcha_cfg.setdefault("api_key", "")
        captcha_cfg.setdefault("selected_model", DEFAULT_GEMINI_MODEL)
        global_cfg["captcha"] = captcha_cfg
        return config_dict

    def _apply_legacy_global_overrides(self, raw: dict, migrated: dict) -> None:
        legacy_system = raw.get("system", {}) if isinstance(raw, dict) else {}
        if isinstance(legacy_system, dict):
            if "mouse_id" in legacy_system:
                migrated["global"]["mouse_id"] = legacy_system.get("mouse_id", 11)
            if "always_on_top" in legacy_system:
                migrated["global"]["always_on_top"] = legacy_system.get("always_on_top", True)

        legacy_captcha = raw.get("captcha", {}) if isinstance(raw, dict) else {}
        if isinstance(legacy_captcha, dict):
            migrated["global"]["captcha"]["enabled"] = bool(legacy_captcha.get("enabled", True))
            migrated["global"]["captcha"]["api_key"] = str(legacy_captcha.get("api_key", ""))
            selected_model = str(legacy_captcha.get("selected_model", DEFAULT_GEMINI_MODEL)).strip()
            migrated["global"]["captcha"]["selected_model"] = selected_model or DEFAULT_GEMINI_MODEL

    def __init__(self, config_path: str = "config.json"):
        super().__init__()
        self._config_path = config_path
        self._config = self._load_config()
        self._region_selector_overlay = None

        # Runtime state
        self._is_running = False
        self._status_text = "Select slot windows..."
        self._destroyed_count = 0
        self._elapsed_time = "00:00:00"
        self._log_lines: list[str] = []
        self._window_list: list[str] = []
        self._mouse_calibration_in_progress = False

        self._active_config_client = int(self._global_cfg("active_config_client", 0) or 0)
        if self._active_config_client not in (0, 1):
            self._active_config_client = 0

        # Process manager (lazy init)
        self._process_manager = None
        self._discovered_windows = []

        # Per-slot selected HWND and display name
        self._selected_hwnd: Dict[str, Optional[int]] = {
            "client_1": None,
            "client_2": None,
        }
        self._attached_window_name: Dict[str, str] = {
            "client_1": "No window",
            "client_2": "No window",
        }

        # Keep show-all in global config
        self._show_all_windows = bool(self._global_cfg("show_all_windows", False))

        # Bot signals
        self.bot_signals = BotSignals()
        self.bot_signals.log_message.connect(self._append_log)
        self.bot_signals.update_stat.connect(self._on_stat_update)
        self.bot_signals.bot_stopped.connect(self._on_bot_stopped)

        # Worker refs
        self._worker_thread = None
        self._worker = None
        self._start_time = 0
        self._timer_running = False

        # Dynamic skill entries for active client/profile
        self._skill_entries_cache: List[dict] = []
        self._refresh_skill_entries_cache(save_if_changed=True, emit_signal=False)

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def _default_client_config(self, default_profile: str) -> dict:
        return {
            "general": {
                "auto_attack": True,
                "auto_loot": True,
                "auto_revive": True,
                "captcha_solver": True,
                "revive_delay": 1,
                "combat_timeout": 120,
                "scan_radius": 500,
                "anti_stuck": True,
                "debug_mode": False,
                "miss_timeout": 2,
                "movement_timeout": 8,
                "verify_timeout": 3,
            },
            "vision": {
                "model_path": "models/metin2_yolo26.onnx",
                "fallback_model": "models/best.pt",
                "confidence_threshold": 0.5,
                "yolo_confidence": 0.45,
                "mask_regions": [],
            },
            "skills": {
                "use_skills": True,
                "check_interval": 7,
                "active_profile": default_profile,
                "profile_bindings": {
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
                },
                "skill_1": {"key": "4", "cooldown": 60, "enabled": True},
                "skill_2": {"key": "f2", "cooldown": 180, "enabled": True},
                "skill_3": {"key": "3", "cooldown": 20, "enabled": False},
                "buff_1": {"key": "f3", "cooldown": 200, "enabled": False},
                "buff_2": {"key": "f4", "cooldown": 200, "enabled": False},
            },
            "system": {
                "last_hwnd": 0,
                "last_pid": 0,
            },
            "combat": {
                "selection_mode": "Nearest",
                "selection_mode_internal": "nearest",
                "strafe_start_delay": 2,
                "strafe_interval": 1,
                "multi_target_queue_enabled": False,
                "multi_target_queue_size": 3,
                "deferred_queue_click_delay_sec": 3,
            },
            "quest": {
                "enabled": False,
                "check_interval": 3,
            },
        }

    def _default_dual_config(self) -> dict:
        return {
            "client_1": self._default_client_config("savasci_bedensel"),
            "client_2": self._default_client_config("saman_ejderha"),
            "global": {
                "mouse_id": 11,
                "always_on_top": True,
                "show_all_windows": False,
                "active_config_client": 0,
                "captcha": {
                    "enabled": True,
                    "api_key": "",
                    "selected_model": DEFAULT_GEMINI_MODEL,
                },
            },
        }

    def _merge_with_defaults(self, raw, defaults):
        merged = copy.deepcopy(defaults)
        if not isinstance(raw, dict):
            return merged

        for key, value in raw.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._merge_with_defaults(value, merged[key])
            else:
                merged[key] = value
        return merged

    def _load_config(self) -> dict:
        defaults = self._default_dual_config()

        if not os.path.exists(self._config_path):
            return defaults

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return defaults

        if isinstance(raw, dict) and "client_1" in raw and "client_2" in raw:
            merged = self._merge_with_defaults(raw, defaults)
            return self._ensure_global_captcha_defaults(merged)

        # Legacy flat schema migration -> duplicate into both clients.
        migrated = copy.deepcopy(defaults)
        migrated["client_1"] = self._merge_with_defaults(raw, migrated["client_1"])
        migrated["client_2"] = self._merge_with_defaults(raw, migrated["client_2"])

        # Preserve a different default profile for client_2 unless explicitly set.
        raw_skills = raw.get("skills", {}) if isinstance(raw, dict) else {}
        if "active_profile" not in raw_skills:
            migrated["client_2"]["skills"]["active_profile"] = "saman_ejderha"

        self._apply_legacy_global_overrides(raw, migrated)

        return self._ensure_global_captcha_defaults(migrated)

    def _save(self):
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[Config] Save error: {e}")

    def _safe_int(self, value) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _active_client_key(self) -> str:
        return "client_1" if self._active_config_client == 0 else "client_2"

    def _client_cfg(self, client_key: str, section: str, key: str, default=None):
        return self._config.get(client_key, {}).get(section, {}).get(key, default)

    def _cfg(self, section: str, key: str, default=None):
        return self._client_cfg(self._active_client_key(), section, key, default)

    def _set_client_cfg(self, client_key: str, section: str, key: str, value):
        self._config.setdefault(client_key, {}).setdefault(section, {})[key] = value

    def _set_cfg(self, section: str, key: str, value):
        self._set_client_cfg(self._active_client_key(), section, key, value)

    def _global_cfg(self, key: str, default=None):
        return self._config.get("global", {}).get(key, default)

    def _set_global_cfg(self, key: str, value):
        self._config.setdefault("global", {})[key] = value

    def _discover_skill_profiles(self):
        root = os.path.join(os.getcwd(), "assets", "skills")
        profiles = []
        try:
            if os.path.isdir(root):
                for item in sorted(os.listdir(root)):
                    full_path = os.path.join(root, item)
                    if os.path.isdir(full_path):
                        profiles.append(item)
        except Exception:
            pass

        if not profiles:
            profiles = ["savasci_bedensel", "saman_ejderha", "saman_iyilestirme"]
        return profiles

    def _is_valid_skill_slot(self, slot_name: str) -> bool:
        if not isinstance(slot_name, str):
            return False
        return bool(self._SLOT_NAME_PATTERN.match(slot_name.strip().lower()))

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

    def _format_skill_display_name(self, filename: str) -> str:
        base_name, _ext = os.path.splitext(str(filename))
        normalized = base_name.replace("_", " ").replace("-", " ").strip()
        if not normalized:
            return "Unnamed Skill"
        words = [part.capitalize() for part in normalized.split() if part]
        return " ".join(words) if words else normalized

    def _list_profile_skill_files(self, profile_name: str) -> List[str]:
        files: List[str] = []
        profile_dir = os.path.join(os.getcwd(), "assets", "skills", str(profile_name).strip())

        if not os.path.isdir(profile_dir):
            return files

        try:
            for item in sorted(os.listdir(profile_dir)):
                full_path = os.path.join(profile_dir, item)
                if not os.path.isfile(full_path):
                    continue
                if not item.lower().endswith(self._IMAGE_EXTENSIONS):
                    continue
                files.append(item)
        except Exception:
            return []

        return files

    def _slot_defaults(self, slot_name: str) -> dict:
        key = str(slot_name).strip().lower()
        defaults = {
            "skill_1": {"key": "4", "cooldown": 60, "enabled": True},
            "skill_2": {"key": "f2", "cooldown": 180, "enabled": True},
            "skill_3": {"key": "3", "cooldown": 20, "enabled": False},
            "buff_1": {"key": "f3", "cooldown": 200, "enabled": False},
            "buff_2": {"key": "f4", "cooldown": 200, "enabled": False},
        }
        return copy.deepcopy(defaults.get(key, {"key": "", "cooldown": 60, "enabled": False}))

    def _parse_cooldown(self, value, fallback: int = 60) -> int:
        parsed = self._safe_int(value)
        if parsed <= 0:
            return max(1, int(fallback))
        return parsed

    def _resolve_active_profile(self, skills_cfg: dict) -> Tuple[str, bool]:
        changed = False
        available_profiles = self._discover_skill_profiles()
        active_profile = str(skills_cfg.get("active_profile", "")).strip()

        if not active_profile and available_profiles:
            active_profile = available_profiles[0]
            skills_cfg["active_profile"] = active_profile
            changed = True

        if available_profiles and active_profile not in available_profiles:
            active_profile = available_profiles[0]
            skills_cfg["active_profile"] = active_profile
            changed = True

        return active_profile, changed

    def _canonicalize_profile_bindings(self, raw_profile_map: dict) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if not isinstance(raw_profile_map, dict):
            return normalized

        for raw_slot, raw_filename in raw_profile_map.items():
            slot_name = str(raw_slot).strip().lower()
            filename = str(raw_filename).strip()

            if not self._is_valid_skill_slot(slot_name):
                continue
            if not filename:
                continue

            normalized[slot_name] = filename

        return normalized

    def _next_skill_slot_index(self, bindings: Dict[str, str]) -> int:
        indexes: List[int] = []
        for slot_name in bindings.keys():
            if not slot_name.startswith("skill_"):
                continue
            try:
                indexes.append(int(slot_name.split("_", 1)[1]))
            except ValueError:
                continue

        if not indexes:
            return 1
        return max(indexes) + 1

    def _normalize_profile_bindings_for_files(self, current_map: Dict[str, str], files: List[str]) -> Tuple[Dict[str, str], bool]:
        files_set = set(files)
        normalized: Dict[str, str] = {}
        used_files = set()

        for slot_name, filename in current_map.items():
            if filename not in files_set:
                continue
            if filename in used_files:
                continue

            normalized[slot_name] = filename
            used_files.add(filename)

        next_skill_index = self._next_skill_slot_index(normalized)
        auto_added = False

        for filename in files:
            if filename in used_files:
                continue

            while f"skill_{next_skill_index}" in normalized:
                next_skill_index += 1

            normalized[f"skill_{next_skill_index}"] = filename
            used_files.add(filename)
            next_skill_index += 1
            auto_added = True

        return normalized, auto_added

    def _normalize_slot_config(self, slot_name: str, slot_cfg: dict) -> dict:
        defaults = self._slot_defaults(slot_name)
        source = slot_cfg if isinstance(slot_cfg, dict) else {}

        return {
            "key": str(source.get("key", defaults["key"])).strip().lower(),
            "cooldown": self._parse_cooldown(source.get("cooldown", defaults["cooldown"]), defaults["cooldown"]),
            "enabled": bool(source.get("enabled", defaults["enabled"])),
        }

    def _build_skill_entries(self, skills_cfg: dict, active_profile: str, bindings: Dict[str, str]) -> Tuple[List[dict], bool]:
        changed = False
        entries: List[dict] = []
        profile_dir = os.path.join(os.getcwd(), "assets", "skills", active_profile)

        for slot_name in sorted(bindings.keys(), key=self._slot_sort_key):
            filename = bindings.get(slot_name)
            if not filename:
                continue

            icon_path = os.path.join(profile_dir, filename)
            if not os.path.isfile(icon_path):
                continue

            current_cfg = skills_cfg.get(slot_name, {})
            normalized_cfg = self._normalize_slot_config(slot_name, current_cfg)

            if current_cfg != normalized_cfg:
                skills_cfg[slot_name] = normalized_cfg
                changed = True

            entries.append(
                {
                    "slotId": slot_name,
                    "displayName": self._format_skill_display_name(filename),
                    "iconPath": QUrl.fromLocalFile(os.path.abspath(icon_path)).toString(),
                    "iconFile": filename,
                    "key": normalized_cfg["key"],
                    "cooldown": normalized_cfg["cooldown"],
                    "enabled": normalized_cfg["enabled"],
                }
            )

        return entries, changed

    def _sync_profile_bindings_and_entries(self) -> Tuple[List[dict], bool]:
        changed = False
        client_key = self._active_client_key()
        skills_cfg = self._config.setdefault(client_key, {}).setdefault("skills", {})

        active_profile, profile_changed = self._resolve_active_profile(skills_cfg)
        if profile_changed:
            changed = True

        if not active_profile:
            return [], changed

        files = self._list_profile_skill_files(active_profile)

        profile_bindings = skills_cfg.setdefault("profile_bindings", {})
        raw_profile_map = profile_bindings.get(active_profile, {})
        if not isinstance(raw_profile_map, dict):
            raw_profile_map = {}
            changed = True

        current_map = self._canonicalize_profile_bindings(raw_profile_map)
        normalized_map, auto_added = self._normalize_profile_bindings_for_files(current_map, files)
        if auto_added:
            changed = True

        if normalized_map != current_map:
            changed = True

        profile_bindings[active_profile] = normalized_map

        entries, entry_changed = self._build_skill_entries(skills_cfg, active_profile, normalized_map)
        if entry_changed:
            changed = True

        return entries, changed

    def _refresh_skill_entries_cache(self, save_if_changed: bool, emit_signal: bool) -> None:
        entries, changed = self._sync_profile_bindings_and_entries()
        self._skill_entries_cache = entries

        if changed and save_if_changed:
            self._save()

        if emit_signal:
            self.skillEntriesChanged.emit()

    def _reload_runtime_profile_for_client(self, client_key: str, profile_name: str) -> None:
        worker = self._worker
        if worker is None:
            return

        engine = getattr(worker, "engine", None)
        if engine is None:
            return

        try:
            contexts = getattr(engine, "contexts", [])
            for context in contexts:
                if getattr(context, "slot", "") != client_key:
                    continue

                skills_cfg = context.config.setdefault("skills", {}) if hasattr(context, "config") else {}
                if isinstance(skills_cfg, dict):
                    skills_cfg["active_profile"] = profile_name

                context.active_profile = profile_name
                skill_manager = getattr(context, "skill_manager", None)
                if skill_manager is None:
                    return

                skill_manager.load_profile_templates(
                    profile_name,
                    log_callback=lambda message: self._append_log(message),
                )
                self._append_log(f"[SKILL] Runtime profile reloaded for {client_key}: {profile_name}")
                return
        except Exception as reload_error:
            self._append_log(f"[SKILL] [WARN] Runtime profile reload failed: {reload_error}")

    def _emit_active_client_config_changed(self):
        self.yoloConfidenceChanged.emit()
        self.maskRegionCountChanged.emit()
        self.selectionModeIndexChanged.emit()
        self.combatTimeoutChanged.emit()
        self.scanRadiusChanged.emit()
        self.reviveDelayChanged.emit()
        self.skillCheckIntervalChanged.emit()
        self.activeSkillProfileChanged.emit()
        self.questCheckIntervalChanged.emit()
        self.missTimeoutChanged.emit()
        self.movementTimeoutChanged.emit()
        self.verifyTimeoutChanged.emit()
        self.strafeStartDelayChanged.emit()
        self.strafeIntervalChanged.emit()
        self.multiTargetQueueEnabledChanged.emit()
        self.multiTargetQueueSizeChanged.emit()
        self.deferredQueueClickDelayChanged.emit()
        self.autoLootChanged.emit()
        self.autoSkillsChanged.emit()
        self.autoReviveChanged.emit()
        self.antiStuckChanged.emit()
        self.captchaSolverChanged.emit()
        self.captchaEnabledChanged.emit()
        self.captchaApiKeyChanged.emit()
        self.captchaSelectedModelChanged.emit()
        self.questEnabledChanged.emit()
        self.debugModeChanged.emit()
        self.skill1KeyChanged.emit()
        self.skill1CooldownChanged.emit()
        self.skill1EnabledChanged.emit()
        self.skill2KeyChanged.emit()
        self.skill2CooldownChanged.emit()
        self.skill2EnabledChanged.emit()
        self.skill3KeyChanged.emit()
        self.skill3CooldownChanged.emit()
        self.skill3EnabledChanged.emit()
        self.buff1KeyChanged.emit()
        self.buff1CooldownChanged.emit()
        self.buff1EnabledChanged.emit()
        self.buff2KeyChanged.emit()
        self.buff2CooldownChanged.emit()
        self.buff2EnabledChanged.emit()
        self.skillEntriesChanged.emit()

    # ------------------------------------------------------------------
    # Runtime properties
    # ------------------------------------------------------------------

    def _get_is_running(self):
        return self._is_running

    isRunning = Property(bool, _get_is_running, notify=isRunningChanged)

    def _get_is_attached(self):
        slot1_hwnd = self._safe_int(self._selected_hwnd.get("client_1"))
        slot2_hwnd = self._safe_int(self._selected_hwnd.get("client_2"))
        return slot1_hwnd > 0 or slot2_hwnd > 0

    isAttached = Property(bool, _get_is_attached, notify=isAttachedChanged)

    def _get_slot1_attached(self):
        return self._safe_int(self._selected_hwnd.get("client_1")) > 0

    slot1Attached = Property(bool, _get_slot1_attached, notify=slot1AttachedChanged)

    def _get_slot2_attached(self):
        return self._safe_int(self._selected_hwnd.get("client_2")) > 0

    slot2Attached = Property(bool, _get_slot2_attached, notify=slot2AttachedChanged)

    def _get_slot1_window_name(self):
        return self._attached_window_name["client_1"]

    slot1WindowName = Property(str, _get_slot1_window_name, notify=slot1WindowNameChanged)

    def _get_slot2_window_name(self):
        return self._attached_window_name["client_2"]

    slot2WindowName = Property(str, _get_slot2_window_name, notify=slot2WindowNameChanged)

    def _get_attached_window_name(self):
        return f"C1: {self._attached_window_name['client_1']} | C2: {self._attached_window_name['client_2']}"

    attachedWindowName = Property(str, _get_attached_window_name, notify=attachedWindowNameChanged)

    def _get_status_text(self):
        return self._status_text

    statusText = Property(str, _get_status_text, notify=statusTextChanged)

    def _get_destroyed_count(self):
        return self._destroyed_count

    destroyedCount = Property(int, _get_destroyed_count, notify=destroyedCountChanged)

    def _get_elapsed_time(self):
        return self._elapsed_time

    elapsedTime = Property(str, _get_elapsed_time, notify=elapsedTimeChanged)

    def _get_log_text(self):
        return "\n".join(self._log_lines[-500:])

    logText = Property(str, _get_log_text, notify=logTextChanged)

    def _get_window_list(self):
        return self._window_list

    windowList = Property(list, _get_window_list, notify=windowListChanged)

    def _get_active_config_client(self):
        return self._active_config_client

    def _set_active_config_client(self, value):
        idx = 1 if int(value) == 1 else 0
        if idx == self._active_config_client:
            return
        self._active_config_client = idx
        self._set_global_cfg("active_config_client", idx)
        self._refresh_skill_entries_cache(save_if_changed=False, emit_signal=False)
        self._save()
        self.activeConfigClientChanged.emit()
        self._emit_active_client_config_changed()

    activeConfigClient = Property(
        int,
        _get_active_config_client,
        _set_active_config_client,
        notify=activeConfigClientChanged,
    )

    # ------------------------------------------------------------------
    # Config properties (active client scoped)
    # ------------------------------------------------------------------

    def _get_yolo_confidence(self):
        return float(self._cfg("vision", "yolo_confidence", 0.45))

    def _set_yolo_confidence(self, v):
        self._set_cfg("vision", "yolo_confidence", round(float(v), 2))
        self._save()
        self.yoloConfidenceChanged.emit()

    yoloConfidence = Property(float, _get_yolo_confidence, _set_yolo_confidence, notify=yoloConfidenceChanged)

    def _normalize_mask_regions(self, regions) -> List[dict]:
        normalized: List[dict] = []
        if not isinstance(regions, list):
            return normalized

        for item in regions:
            if not isinstance(item, dict):
                continue

            width = self._safe_int(item.get("width", 0))
            height = self._safe_int(item.get("height", 0))
            if width <= 0 or height <= 0:
                continue

            x = max(0, self._safe_int(item.get("x", 0)))
            y = max(0, self._safe_int(item.get("y", 0)))

            normalized.append(
                {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
            )

        return normalized

    def _get_mask_regions_for_client(self, client_key: str) -> List[dict]:
        raw = self._client_cfg(client_key, "vision", "mask_regions", [])
        return self._normalize_mask_regions(raw)

    def _set_mask_regions_for_client(self, client_key: str, regions) -> List[dict]:
        normalized = self._normalize_mask_regions(regions)
        self._set_client_cfg(client_key, "vision", "mask_regions", normalized)
        self._save()
        self._reload_runtime_mask_regions_for_client(client_key, normalized)
        return normalized

    def _reload_runtime_mask_regions_for_client(self, client_key: str, regions: List[dict]) -> None:
        worker = self._worker
        if worker is None:
            return

        engine = getattr(worker, "engine", None)
        if engine is None:
            return

        try:
            contexts = getattr(engine, "contexts", [])
            for context in contexts:
                if getattr(context, "slot", "") != client_key:
                    continue

                if not hasattr(context, "config"):
                    return

                vision_cfg = context.config.setdefault("vision", {})
                if not isinstance(vision_cfg, dict):
                    return

                vision_cfg["mask_regions"] = list(regions)
                self._append_log(f"[VISION] Runtime ROI masks updated for {client_key}: {len(regions)}")
                return
        except Exception as reload_error:
            self._append_log(f"[VISION] [WARN] Runtime ROI mask reload failed: {reload_error}")

    def _resolve_client_hwnd(self, client_key: str) -> Optional[int]:
        selected = self._safe_int(self._selected_hwnd.get(client_key))
        if selected > 0:
            return selected

        try:
            if self._process_manager is None:
                from core.process_manager import get_process_manager

                self._process_manager = get_process_manager()

            if self._process_manager is None:
                return None

            fallback = self._process_manager.get_locked_hwnd(slot=client_key)
            fallback_int = self._safe_int(fallback)
            return fallback_int if fallback_int > 0 else None
        except Exception:
            return None

    def _get_region_selector_class(self):
        from ui.region_selector_overlay import RegionSelectorOverlay

        return RegionSelectorOverlay

    def _close_region_selector_overlay(self) -> None:
        selector = self._region_selector_overlay
        self._region_selector_overlay = None
        if selector is None:
            return

        try:
            selector.close()
        except Exception:
            pass

        try:
            selector.deleteLater()
        except Exception:
            pass

    def _on_mask_regions_saved(self, client_key: str, regions) -> None:
        normalized = self._set_mask_regions_for_client(client_key, regions)
        if client_key == self._active_client_key():
            self.maskRegionCountChanged.emit()
        self._append_log(f"[VISION] Saved {len(normalized)} ROI mask region(s) for {client_key}.")
        self._close_region_selector_overlay()

    def _on_mask_regions_cancelled(self) -> None:
        self._append_log("[VISION] ROI mask selection cancelled.")
        self._close_region_selector_overlay()

    @Slot(object)
    def _on_mask_overlay_destroyed(self, _obj=None):
        self._region_selector_overlay = None

    def _get_mask_region_count(self):
        return len(self._get_mask_regions_for_client(self._active_client_key()))

    maskRegionCount = Property(int, _get_mask_region_count, notify=maskRegionCountChanged)

    _SELECTION_MODES = ["Nearest", "Random", "Largest"]
    _SELECTION_INTERNAL = {"Nearest": "nearest", "Random": "random", "Largest": "largest"}

    def _get_selection_mode_index(self):
        mode = self._cfg("combat", "selection_mode", "Nearest")
        return self._SELECTION_MODES.index(mode) if mode in self._SELECTION_MODES else 0

    def _set_selection_mode_index(self, idx):
        if 0 <= idx < len(self._SELECTION_MODES):
            mode = self._SELECTION_MODES[idx]
            self._set_cfg("combat", "selection_mode", mode)
            self._set_cfg("combat", "selection_mode_internal", self._SELECTION_INTERNAL[mode])
            self._save()
            self.selectionModeIndexChanged.emit()

    selectionModeIndex = Property(int, _get_selection_mode_index, _set_selection_mode_index, notify=selectionModeIndexChanged)

    def _get_combat_timeout(self):
        return int(self._cfg("general", "combat_timeout", 120))

    def _set_combat_timeout(self, v):
        self._set_cfg("general", "combat_timeout", int(v))
        self._save()
        self.combatTimeoutChanged.emit()

    combatTimeout = Property(int, _get_combat_timeout, _set_combat_timeout, notify=combatTimeoutChanged)

    def _get_scan_radius(self):
        return int(self._cfg("general", "scan_radius", 500))

    def _set_scan_radius(self, v):
        self._set_cfg("general", "scan_radius", int(v))
        self._save()
        self.scanRadiusChanged.emit()

    scanRadius = Property(int, _get_scan_radius, _set_scan_radius, notify=scanRadiusChanged)

    def _get_revive_delay(self):
        return int(self._cfg("general", "revive_delay", 1))

    def _set_revive_delay(self, v):
        self._set_cfg("general", "revive_delay", int(v))
        self._save()
        self.reviveDelayChanged.emit()

    reviveDelay = Property(int, _get_revive_delay, _set_revive_delay, notify=reviveDelayChanged)

    def _get_skill_check_interval(self):
        return int(self._cfg("skills", "check_interval", 7))

    def _set_skill_check_interval(self, v):
        self._set_cfg("skills", "check_interval", int(v))
        self._save()
        self.skillCheckIntervalChanged.emit()

    skillCheckInterval = Property(int, _get_skill_check_interval, _set_skill_check_interval, notify=skillCheckIntervalChanged)

    def _get_available_skill_profiles(self):
        return self._discover_skill_profiles()

    availableSkillProfiles = Property(list, _get_available_skill_profiles, notify=availableSkillProfilesChanged)

    def _get_active_skill_profile(self):
        profile = str(self._cfg("skills", "active_profile", "savasci_bedensel"))
        available = self._discover_skill_profiles()
        if profile not in available and available:
            profile = available[0]
        return profile

    def _set_active_skill_profile(self, profile):
        client_key = self._active_client_key()
        profile_str = str(profile).strip()
        available = self._discover_skill_profiles()
        if available and profile_str not in available:
            profile_str = available[0]
        self._set_cfg("skills", "active_profile", profile_str)
        self._refresh_skill_entries_cache(save_if_changed=False, emit_signal=False)
        self._save()
        self._reload_runtime_profile_for_client(client_key, profile_str)
        self.activeSkillProfileChanged.emit()
        self.skillEntriesChanged.emit()

    activeSkillProfile = Property(str, _get_active_skill_profile, _set_active_skill_profile, notify=activeSkillProfileChanged)

    def _get_skill_entries(self):
        return list(self._skill_entries_cache)

    skillEntries = Property(list, _get_skill_entries, notify=skillEntriesChanged)

    def _get_quest_check_interval(self):
        return int(self._cfg("quest", "check_interval", 3))

    def _set_quest_check_interval(self, v):
        self._set_cfg("quest", "check_interval", int(v))
        self._save()
        self.questCheckIntervalChanged.emit()

    questCheckInterval = Property(int, _get_quest_check_interval, _set_quest_check_interval, notify=questCheckIntervalChanged)

    def _get_miss_timeout(self):
        return int(self._cfg("general", "miss_timeout", 2))

    def _set_miss_timeout(self, v):
        self._set_cfg("general", "miss_timeout", int(v))
        self._save()
        self.missTimeoutChanged.emit()

    missTimeout = Property(int, _get_miss_timeout, _set_miss_timeout, notify=missTimeoutChanged)

    def _get_movement_timeout(self):
        return int(self._cfg("general", "movement_timeout", 8))

    def _set_movement_timeout(self, v):
        self._set_cfg("general", "movement_timeout", int(v))
        self._save()
        self.movementTimeoutChanged.emit()

    movementTimeout = Property(int, _get_movement_timeout, _set_movement_timeout, notify=movementTimeoutChanged)

    def _get_verify_timeout(self):
        return int(self._cfg("general", "verify_timeout", 3))

    def _set_verify_timeout(self, v):
        self._set_cfg("general", "verify_timeout", int(v))
        self._save()
        self.verifyTimeoutChanged.emit()

    verifyTimeout = Property(int, _get_verify_timeout, _set_verify_timeout, notify=verifyTimeoutChanged)

    def _get_strafe_start_delay(self):
        return int(self._cfg("combat", "strafe_start_delay", 2))

    def _set_strafe_start_delay(self, v):
        self._set_cfg("combat", "strafe_start_delay", int(v))
        self._save()
        self.strafeStartDelayChanged.emit()

    strafeStartDelay = Property(int, _get_strafe_start_delay, _set_strafe_start_delay, notify=strafeStartDelayChanged)

    def _get_strafe_interval(self):
        return int(self._cfg("combat", "strafe_interval", 1))

    def _set_strafe_interval(self, v):
        self._set_cfg("combat", "strafe_interval", int(v))
        self._save()
        self.strafeIntervalChanged.emit()

    strafeInterval = Property(int, _get_strafe_interval, _set_strafe_interval, notify=strafeIntervalChanged)

    def _get_multi_target_queue_enabled(self):
        return bool(self._cfg("combat", "multi_target_queue_enabled", False))

    def _set_multi_target_queue_enabled(self, v):
        self._set_cfg("combat", "multi_target_queue_enabled", bool(v))
        self._save()
        self.multiTargetQueueEnabledChanged.emit()

    multiTargetQueueEnabled = Property(
        bool,
        _get_multi_target_queue_enabled,
        _set_multi_target_queue_enabled,
        notify=multiTargetQueueEnabledChanged,
    )

    def _get_multi_target_queue_size(self):
        raw_size = self._safe_int(self._cfg("combat", "multi_target_queue_size", 3))
        return max(1, raw_size)

    def _set_multi_target_queue_size(self, v):
        size = max(1, self._safe_int(v))
        self._set_cfg("combat", "multi_target_queue_size", size)
        self._save()
        self.multiTargetQueueSizeChanged.emit()

    multiTargetQueueSize = Property(
        int,
        _get_multi_target_queue_size,
        _set_multi_target_queue_size,
        notify=multiTargetQueueSizeChanged,
    )

    def _get_deferred_queue_click_delay(self):
        raw_delay = self._safe_int(self._cfg("combat", "deferred_queue_click_delay_sec", 3))
        return max(0, raw_delay)

    def _set_deferred_queue_click_delay(self, v):
        delay_sec = max(0, self._safe_int(v))
        self._set_cfg("combat", "deferred_queue_click_delay_sec", delay_sec)
        self._save()
        self.deferredQueueClickDelayChanged.emit()

    deferredQueueClickDelay = Property(
        int,
        _get_deferred_queue_click_delay,
        _set_deferred_queue_click_delay,
        notify=deferredQueueClickDelayChanged,
    )

    def _get_auto_loot(self):
        return bool(self._cfg("general", "auto_loot", True))

    def _set_auto_loot(self, v):
        self._set_cfg("general", "auto_loot", bool(v))
        self._save()
        self.autoLootChanged.emit()

    autoLoot = Property(bool, _get_auto_loot, _set_auto_loot, notify=autoLootChanged)

    def _get_auto_skills(self):
        return bool(self._cfg("skills", "use_skills", True))

    def _set_auto_skills(self, v):
        self._set_cfg("skills", "use_skills", bool(v))
        self._save()
        self.autoSkillsChanged.emit()

    autoSkills = Property(bool, _get_auto_skills, _set_auto_skills, notify=autoSkillsChanged)

    def _get_auto_revive(self):
        return bool(self._cfg("general", "auto_revive", True))

    def _set_auto_revive(self, v):
        self._set_cfg("general", "auto_revive", bool(v))
        self._save()
        self.autoReviveChanged.emit()

    autoRevive = Property(bool, _get_auto_revive, _set_auto_revive, notify=autoReviveChanged)

    def _get_anti_stuck(self):
        return bool(self._cfg("general", "anti_stuck", True))

    def _set_anti_stuck(self, v):
        self._set_cfg("general", "anti_stuck", bool(v))
        self._save()
        self.antiStuckChanged.emit()

    antiStuck = Property(bool, _get_anti_stuck, _set_anti_stuck, notify=antiStuckChanged)

    def _get_captcha_solver(self):
        return bool(self._cfg("general", "captcha_solver", True))

    def _set_captcha_solver(self, v):
        enabled = bool(v)
        self._set_cfg("general", "captcha_solver", enabled)
        self._set_client_cfg("client_1", "general", "captcha_solver", enabled)
        self._set_client_cfg("client_2", "general", "captcha_solver", enabled)

        captcha_cfg = self._global_cfg("captcha", {})
        if not isinstance(captcha_cfg, dict):
            captcha_cfg = {}
        captcha_cfg["enabled"] = enabled
        self._set_global_cfg("captcha", captcha_cfg)
        self.captchaEnabledChanged.emit()
        self._save()
        self.captchaSolverChanged.emit()

    captchaSolver = Property(bool, _get_captcha_solver, _set_captcha_solver, notify=captchaSolverChanged)

    def _get_captcha_enabled(self):
        return bool(self._global_cfg("captcha", {}).get("enabled", True))

    def _set_captcha_enabled(self, v):
        enabled = bool(v)
        captcha_cfg = self._global_cfg("captcha", {})
        if not isinstance(captcha_cfg, dict):
            captcha_cfg = {}
        captcha_cfg["enabled"] = enabled
        self._set_global_cfg("captcha", captcha_cfg)
        self._set_client_cfg("client_1", "general", "captcha_solver", enabled)
        self._set_client_cfg("client_2", "general", "captcha_solver", enabled)
        self._save()
        self.captchaEnabledChanged.emit()
        self.captchaSolverChanged.emit()

    captchaEnabled = Property(bool, _get_captcha_enabled, _set_captcha_enabled, notify=captchaEnabledChanged)

    def _get_captcha_api_key(self):
        return str(self._global_cfg("captcha", {}).get("api_key", ""))

    def _set_captcha_api_key(self, v):
        captcha_cfg = self._global_cfg("captcha", {})
        if not isinstance(captcha_cfg, dict):
            captcha_cfg = {}
        captcha_cfg["api_key"] = str(v)
        self._set_global_cfg("captcha", captcha_cfg)
        self._save()
        self.captchaApiKeyChanged.emit()

    captchaApiKey = Property(str, _get_captcha_api_key, _set_captcha_api_key, notify=captchaApiKeyChanged)

    def _get_captcha_selected_model(self):
        return str(self._global_cfg("captcha", {}).get("selected_model", DEFAULT_GEMINI_MODEL))

    def _set_captcha_selected_model(self, v):
        model_name = str(v).strip() or DEFAULT_GEMINI_MODEL
        captcha_cfg = self._global_cfg("captcha", {})
        if not isinstance(captcha_cfg, dict):
            captcha_cfg = {}
        captcha_cfg["selected_model"] = model_name
        self._set_global_cfg("captcha", captcha_cfg)
        self._save()
        self.captchaSelectedModelChanged.emit()

    captchaSelectedModel = Property(
        str,
        _get_captcha_selected_model,
        _set_captcha_selected_model,
        notify=captchaSelectedModelChanged,
    )

    def _get_quest_enabled(self):
        return bool(self._cfg("quest", "enabled", False))

    def _set_quest_enabled(self, v):
        self._set_cfg("quest", "enabled", bool(v))
        self._save()
        self.questEnabledChanged.emit()

    questEnabled = Property(bool, _get_quest_enabled, _set_quest_enabled, notify=questEnabledChanged)

    def _get_debug_mode(self):
        return bool(self._cfg("general", "debug_mode", False))

    def _set_debug_mode(self, v):
        self._set_cfg("general", "debug_mode", bool(v))
        self._save()
        self.debugModeChanged.emit()

    debugMode = Property(bool, _get_debug_mode, _set_debug_mode, notify=debugModeChanged)

    # Global settings
    def _get_mouse_id(self):
        return int(self._global_cfg("mouse_id", 11))

    def _set_mouse_id(self, v):
        self._set_global_cfg("mouse_id", int(v))
        self._save()
        self.mouseIdChanged.emit()

    mouseId = Property(int, _get_mouse_id, _set_mouse_id, notify=mouseIdChanged)

    def _get_always_on_top(self):
        return bool(self._global_cfg("always_on_top", True))

    def _set_always_on_top(self, v):
        self._set_global_cfg("always_on_top", bool(v))
        self._save()
        self.alwaysOnTopChanged.emit()

    alwaysOnTop = Property(bool, _get_always_on_top, _set_always_on_top, notify=alwaysOnTopChanged)

    # Skill slot helpers
    def _skill_get(self, slot, field, default):
        return self._cfg("skills", slot, {}).get(field, default)

    def _skill_set(self, slot, field, value, signal):
        skills = self._config.setdefault(self._active_client_key(), {}).setdefault("skills", {})
        skills.setdefault(slot, {})[field] = value
        self._refresh_skill_entries_cache(save_if_changed=False, emit_signal=False)
        self._save()
        signal.emit()
        self.skillEntriesChanged.emit()

    def _emit_slot_field_changed(self, slot_name: str, field_name: str) -> None:
        signal_map = {
            ("skill_1", "key"): self.skill1KeyChanged,
            ("skill_1", "cooldown"): self.skill1CooldownChanged,
            ("skill_1", "enabled"): self.skill1EnabledChanged,
            ("skill_2", "key"): self.skill2KeyChanged,
            ("skill_2", "cooldown"): self.skill2CooldownChanged,
            ("skill_2", "enabled"): self.skill2EnabledChanged,
            ("skill_3", "key"): self.skill3KeyChanged,
            ("skill_3", "cooldown"): self.skill3CooldownChanged,
            ("skill_3", "enabled"): self.skill3EnabledChanged,
            ("buff_1", "key"): self.buff1KeyChanged,
            ("buff_1", "cooldown"): self.buff1CooldownChanged,
            ("buff_1", "enabled"): self.buff1EnabledChanged,
            ("buff_2", "key"): self.buff2KeyChanged,
            ("buff_2", "cooldown"): self.buff2CooldownChanged,
            ("buff_2", "enabled"): self.buff2EnabledChanged,
        }

        notify_signal = signal_map.get((slot_name, field_name))
        if notify_signal:
            notify_signal.emit()

    def _get_skill1_key(self):
        return self._skill_get("skill_1", "key", "4")

    def _set_skill1_key(self, v):
        self._skill_set("skill_1", "key", v, self.skill1KeyChanged)

    skill1Key = Property(str, _get_skill1_key, _set_skill1_key, notify=skill1KeyChanged)

    def _get_skill1_cooldown(self):
        return int(self._skill_get("skill_1", "cooldown", 60))

    def _set_skill1_cooldown(self, v):
        self._skill_set("skill_1", "cooldown", int(v), self.skill1CooldownChanged)

    skill1Cooldown = Property(int, _get_skill1_cooldown, _set_skill1_cooldown, notify=skill1CooldownChanged)

    def _get_skill1_enabled(self):
        return bool(self._skill_get("skill_1", "enabled", True))

    def _set_skill1_enabled(self, v):
        self._skill_set("skill_1", "enabled", bool(v), self.skill1EnabledChanged)

    skill1Enabled = Property(bool, _get_skill1_enabled, _set_skill1_enabled, notify=skill1EnabledChanged)

    def _get_skill2_key(self):
        return self._skill_get("skill_2", "key", "f2")

    def _set_skill2_key(self, v):
        self._skill_set("skill_2", "key", v, self.skill2KeyChanged)

    skill2Key = Property(str, _get_skill2_key, _set_skill2_key, notify=skill2KeyChanged)

    def _get_skill2_cooldown(self):
        return int(self._skill_get("skill_2", "cooldown", 180))

    def _set_skill2_cooldown(self, v):
        self._skill_set("skill_2", "cooldown", int(v), self.skill2CooldownChanged)

    skill2Cooldown = Property(int, _get_skill2_cooldown, _set_skill2_cooldown, notify=skill2CooldownChanged)

    def _get_skill2_enabled(self):
        return bool(self._skill_get("skill_2", "enabled", True))

    def _set_skill2_enabled(self, v):
        self._skill_set("skill_2", "enabled", bool(v), self.skill2EnabledChanged)

    skill2Enabled = Property(bool, _get_skill2_enabled, _set_skill2_enabled, notify=skill2EnabledChanged)

    def _get_skill3_key(self):
        return self._skill_get("skill_3", "key", "3")

    def _set_skill3_key(self, v):
        self._skill_set("skill_3", "key", v, self.skill3KeyChanged)

    skill3Key = Property(str, _get_skill3_key, _set_skill3_key, notify=skill3KeyChanged)

    def _get_skill3_cooldown(self):
        return int(self._skill_get("skill_3", "cooldown", 20))

    def _set_skill3_cooldown(self, v):
        self._skill_set("skill_3", "cooldown", int(v), self.skill3CooldownChanged)

    skill3Cooldown = Property(int, _get_skill3_cooldown, _set_skill3_cooldown, notify=skill3CooldownChanged)

    def _get_skill3_enabled(self):
        return bool(self._skill_get("skill_3", "enabled", False))

    def _set_skill3_enabled(self, v):
        self._skill_set("skill_3", "enabled", bool(v), self.skill3EnabledChanged)

    skill3Enabled = Property(bool, _get_skill3_enabled, _set_skill3_enabled, notify=skill3EnabledChanged)

    def _get_buff1_key(self):
        return self._skill_get("buff_1", "key", "f3")

    def _set_buff1_key(self, v):
        self._skill_set("buff_1", "key", v, self.buff1KeyChanged)

    buff1Key = Property(str, _get_buff1_key, _set_buff1_key, notify=buff1KeyChanged)

    def _get_buff1_cooldown(self):
        return int(self._skill_get("buff_1", "cooldown", 200))

    def _set_buff1_cooldown(self, v):
        self._skill_set("buff_1", "cooldown", int(v), self.buff1CooldownChanged)

    buff1Cooldown = Property(int, _get_buff1_cooldown, _set_buff1_cooldown, notify=buff1CooldownChanged)

    def _get_buff1_enabled(self):
        return bool(self._skill_get("buff_1", "enabled", False))

    def _set_buff1_enabled(self, v):
        self._skill_set("buff_1", "enabled", bool(v), self.buff1EnabledChanged)

    buff1Enabled = Property(bool, _get_buff1_enabled, _set_buff1_enabled, notify=buff1EnabledChanged)

    def _get_buff2_key(self):
        return self._skill_get("buff_2", "key", "f4")

    def _set_buff2_key(self, v):
        self._skill_set("buff_2", "key", v, self.buff2KeyChanged)

    buff2Key = Property(str, _get_buff2_key, _set_buff2_key, notify=buff2KeyChanged)

    def _get_buff2_cooldown(self):
        return int(self._skill_get("buff_2", "cooldown", 200))

    def _set_buff2_cooldown(self, v):
        self._skill_set("buff_2", "cooldown", int(v), self.buff2CooldownChanged)

    buff2Cooldown = Property(int, _get_buff2_cooldown, _set_buff2_cooldown, notify=buff2CooldownChanged)

    def _get_buff2_enabled(self):
        return bool(self._skill_get("buff_2", "enabled", False))

    def _set_buff2_enabled(self, v):
        self._skill_set("buff_2", "enabled", bool(v), self.buff2EnabledChanged)

    buff2Enabled = Property(bool, _get_buff2_enabled, _set_buff2_enabled, notify=buff2EnabledChanged)

    # ------------------------------------------------------------------
    # QML slots
    # ------------------------------------------------------------------

    @Slot()
    def refreshSkillEntries(self):
        self._refresh_skill_entries_cache(save_if_changed=True, emit_signal=True)

    def _set_dynamic_skill_field(self, slot_name: str, field_name: str, field_value) -> None:
        slot_key = str(slot_name).strip().lower()
        if not self._is_valid_skill_slot(slot_key):
            return

        skills = self._config.setdefault(self._active_client_key(), {}).setdefault("skills", {})
        defaults = self._slot_defaults(slot_key)
        slot_cfg = skills.get(slot_key, {})
        if not isinstance(slot_cfg, dict):
            slot_cfg = {}

        normalized = {
            "key": str(slot_cfg.get("key", defaults["key"])).strip().lower(),
            "cooldown": self._parse_cooldown(slot_cfg.get("cooldown", defaults["cooldown"]), defaults["cooldown"]),
            "enabled": bool(slot_cfg.get("enabled", defaults["enabled"])),
        }

        if field_name == "key":
            new_value = str(field_value).strip().lower()
        elif field_name == "cooldown":
            new_value = self._parse_cooldown(field_value, defaults["cooldown"])
        elif field_name == "enabled":
            new_value = bool(field_value)
        else:
            return

        if normalized.get(field_name) == new_value:
            return

        normalized[field_name] = new_value

        skills[slot_key] = normalized
        self._refresh_skill_entries_cache(save_if_changed=False, emit_signal=False)
        self._save()
        self._emit_slot_field_changed(slot_key, field_name)
        self.skillEntriesChanged.emit()

    @Slot(str, str)
    def setSkillEntryKey(self, slot_name: str, key: str):
        self._set_dynamic_skill_field(slot_name, "key", key)

    @Slot(str, int)
    def setSkillEntryCooldown(self, slot_name: str, cooldown: int):
        self._set_dynamic_skill_field(slot_name, "cooldown", cooldown)

    @Slot(str, bool)
    def setSkillEntryEnabled(self, slot_name: str, enabled: bool):
        self._set_dynamic_skill_field(slot_name, "enabled", enabled)

    @Slot()
    def saveConfig(self):
        self._save()
        self._append_log("[CONFIG] Settings saved.")

    @Slot()
    def startMaskRegionSelection(self):
        client_key = self._active_client_key()
        hwnd = self._resolve_client_hwnd(client_key)
        if not hwnd:
            self._append_log(f"[VISION] [ERROR] {client_key} is not attached. Attach a window first.")
            return

        selector = self._region_selector_overlay
        if selector is not None:
            try:
                if selector.isVisible():
                    selector.raise_()
                    selector.activateWindow()
                    self._append_log("[VISION] ROI selector is already open.")
                    return
            except Exception:
                self._region_selector_overlay = None

        initial_regions = self._get_mask_regions_for_client(client_key)

        try:
            selector_cls = self._get_region_selector_class()
            selector = selector_cls(target_hwnd=hwnd, initial_regions=initial_regions)
        except Exception as overlay_error:
            self._append_log(f"[VISION] [ERROR] ROI selector could not be opened: {overlay_error}")
            return

        self._region_selector_overlay = selector
        selector.selection_saved.connect(lambda regions, ck=client_key: self._on_mask_regions_saved(ck, regions))
        selector.selection_cancelled.connect(self._on_mask_regions_cancelled)
        selector.destroyed.connect(self._on_mask_overlay_destroyed)
        selector.show()
        selector.raise_()
        selector.activateWindow()
        self._append_log(f"[VISION] ROI selector opened for {client_key}. Enter=save, Esc=cancel.")

    @Slot(str, result="QVariantList")
    def fetch_gemini_models(self, api_key: str):
        try:
            models = get_available_models(api_key=(str(api_key).strip() or None))
            unique_models = list(dict.fromkeys([str(m).strip() for m in models if str(m).strip()]))
            return unique_models or [DEFAULT_GEMINI_MODEL]
        except Exception as e:
            self._append_log(f"[CAPTCHA] [WARN] Model fetch failed: {e}")
            return [DEFAULT_GEMINI_MODEL]

    @Slot()
    def refreshWindows(self):
        try:
            if self._process_manager is None:
                from core.process_manager import get_process_manager

                self._process_manager = get_process_manager()

            windows = self._process_manager.enumerate_game_windows(include_all=self._show_all_windows)
            self._discovered_windows = windows or []
            self._window_list = [w.get_short_name() for w in self._discovered_windows]
            self.windowListChanged.emit()
            self._append_log(f"[SCAN] Found {len(self._discovered_windows)} window(s)")
        except Exception as e:
            self._append_log(f"[ERROR] Window scan: {e}")

    @Slot(bool)
    def setShowAllWindows(self, show_all: bool):
        self._show_all_windows = bool(show_all)
        self._set_global_cfg("show_all_windows", self._show_all_windows)
        self._save()
        self.refreshWindows()

    @Slot(int)
    def attachWindow(self, index: int):
        # Backward compatibility: attach to slot 1
        self.attachWindowToSlot(1, index)

    @Slot(int, int)
    def attachWindowToSlot(self, slot_number: int, window_index: int):
        if window_index < 0 or window_index >= len(self._discovered_windows):
            return

        if self._process_manager is None:
            self._append_log("[ERROR] Process manager is not initialized.")
            return

        slot_key = "client_1" if int(slot_number) == 1 else "client_2"
        window_info = self._discovered_windows[window_index]

        try:
            success = self._process_manager.lock_to_slot(slot_key, window_info)
            if not success:
                self._append_log(f"[ERROR] Could not attach {slot_key}.")
                return

            self._selected_hwnd[slot_key] = window_info.hwnd
            self._attached_window_name[slot_key] = window_info.title[:30]

            self._set_client_cfg(slot_key, "system", "last_hwnd", window_info.hwnd)
            self._set_client_cfg(slot_key, "system", "last_pid", window_info.pid)
            self._save()

            self._append_log(f"[ATTACH] {slot_key} -> {window_info.title} (HWND: {window_info.hwnd})")

            if slot_key == "client_1":
                self.slot1AttachedChanged.emit()
                self.slot1WindowNameChanged.emit()
            else:
                self.slot2AttachedChanged.emit()
                self.slot2WindowNameChanged.emit()

            self.isAttachedChanged.emit()
            self.attachedWindowNameChanged.emit()

            self._status_text = "Ready" if self._get_is_attached() else "Attach at least one slot"
            self.statusTextChanged.emit()

            try:
                label = "Client 1 Attached" if slot_key == "client_1" else "Client 2 Attached"
                self.bot_signals.highlight_window.emit(int(window_info.hwnd), label)
            except Exception:
                pass

        except Exception as e:
            self._append_log(f"[ERROR] Attach: {e}")

    @Slot()
    def toggleBot(self):
        if self._is_running:
            self._stop_bot()
        else:
            self._start_bot()

    @Slot()
    def calibrateMouse(self):
        if self._mouse_calibration_in_progress:
            self._append_log("[MOUSE] Calibration already running")
            return

        self._mouse_calibration_in_progress = True
        self._append_log("[MOUSE] Calibration started")

        def _calibration_worker():
            detected_id = None
            try:
                from core.drivers.interception_handler import auto_detect_mouse_id, find_active_mouse_silent

                detected_id = find_active_mouse_silent()
                if detected_id is None:
                    detected_id = auto_detect_mouse_id(timeout_seconds=2)
                if detected_id is None:
                    detected_id = auto_detect_mouse_id(timeout_seconds=6)
            except Exception as e:
                self._append_log(f"[MOUSE] [ERROR] {e}")
            finally:
                try:
                    if detected_id is not None:
                        self._set_global_cfg("mouse_id", int(detected_id))
                        self._save()
                        self.mouseIdChanged.emit()
                        self._append_log(f"[MOUSE] Detected and saved: {int(detected_id)}")
                    else:
                        self._append_log("[MOUSE] Detection failed. Move mouse and retry.")
                finally:
                    self._mouse_calibration_in_progress = False

        threading.Thread(target=_calibration_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def _build_attached_client_contexts(self) -> List[dict]:
        contexts: List[dict] = []
        for client_key in self.CLIENT_KEYS:
            hwnd = self._safe_int(self._selected_hwnd.get(client_key))
            if hwnd <= 0:
                continue
            contexts.append(
                {
                    "slot": client_key,
                    "hwnd": hwnd,
                    "config": self._config.get(client_key, {}),
                }
            )
        return contexts

    def _start_bot(self):
        client_contexts = self._build_attached_client_contexts()
        if not client_contexts:
            self._append_log("[ERROR] Attach at least one slot before starting.")
            return

        self._is_running = True
        self._status_text = "Running..."
        self._start_time = time.time()
        self.isRunningChanged.emit()
        self.statusTextChanged.emit()

        from PySide6.QtCore import QThread
        from core.dual_client_engine import DualClientBotEngine

        class _Worker(QObject):
            def __init__(self, signals, config, client_contexts):
                super().__init__()
                self.signals = signals
                self.config = config
                self.client_contexts = list(client_contexts)
                self.stop_event = threading.Event()
                self.engine = DualClientBotEngine()

            def run(self):
                def log_cb(msg):
                    self.signals.log_message.emit(msg)

                def overlay_cb(dets):
                    self.signals.update_overlay.emit(dets)

                def overlay_hide_cb(hidden):
                    self.signals.overlay_hide.emit(hidden)

                def stats_cb(stones, elapsed):
                    _ = elapsed
                    self.signals.update_stat.emit("Targets", str(stones))

                def status_cb(text):
                    self.signals.update_stat.emit("Status", str(text))

                global_cfg = self.config.get("global", {})
                mouse_id = int(global_cfg.get("mouse_id", 11))

                client_contexts = copy.deepcopy(self.client_contexts)

                try:
                    self.engine.run_bot_logic(
                        mouse_id=mouse_id,
                        client_contexts=client_contexts,
                        global_config=global_cfg,
                        log_callback=log_cb,
                        stop_event=self.stop_event,
                        stats_callback=stats_cb,
                        overlay_callback=overlay_cb,
                        overlay_hide_callback=overlay_hide_cb,
                        status_callback=status_cb,
                    )
                except Exception as e:
                    self.signals.log_message.emit(f"Worker Error: {e}")
                finally:
                    self.signals.bot_stopped.emit()

            def stop(self):
                self.stop_event.set()

        self._worker_thread = QThread()
        self._worker = _Worker(self.bot_signals, copy.deepcopy(self._config), client_contexts)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.start()

        attached_summary = ", ".join(
            [f"{ctx.get('slot')} HWND: {ctx.get('hwnd')}" for ctx in client_contexts]
        )
        self._append_log(f"[BOT] Started ({attached_summary})")

        self._timer_running = True
        threading.Thread(target=self._timer_loop, daemon=True).start()

    def _stop_bot(self):
        if self._worker:
            self._worker.stop()
            self._status_text = "Stopping..."
            self.statusTextChanged.emit()
            self._append_log("[BOT] Stop requested...")

    def _on_bot_stopped(self):
        self._is_running = False
        self._timer_running = False
        self._status_text = "Stopped"
        self.isRunningChanged.emit()
        self.statusTextChanged.emit()

        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

    def _timer_loop(self):
        while self._timer_running:
            elapsed = time.time() - self._start_time
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self._elapsed_time = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
            self.elapsedTimeChanged.emit()
            time.sleep(1.0)

    def _on_stat_update(self, key: str, value: str):
        if key in ("Targets", "Taslar"):
            try:
                self._destroyed_count = int(value)
                self.destroyedCountChanged.emit()
            except ValueError:
                pass
        elif key == "Status":
            self._status_text = str(value)
            self.statusTextChanged.emit()
        elif key == "Sure":
            self._elapsed_time = value
            self.elapsedTimeChanged.emit()

    def _append_log(self, msg: str):
        self._log_lines.append(msg)
        if len(self._log_lines) > 500:
            self._log_lines = self._log_lines[-500:]
        self.logTextChanged.emit()
