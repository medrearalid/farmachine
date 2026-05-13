import json
import os
import copy
from typing import Dict, Any, Optional

class ConfigManager:
    _CLIENT_TEMPLATE = {
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
            "verify_timeout": 3
        },
        "vision": {
            "model_path": "models/metin2_yolo26.onnx",
            "fallback_model": "models/best.pt",
            "confidence_threshold": 0.5,
            "yolo_confidence": 0.45,
            "mask_regions": []
        },
        "skills": {
            "use_skills": True,
            "check_interval": 7,
            "active_profile": "savasci_bedensel",
            "profile_bindings": {
                "savasci_bedensel": {
                    "skill_1": "aura.png",
                    "skill_2": "berserk.png"
                },
                "saman_ejderha": {
                    "skill_1": "ejderha_yardimi.png",
                    "skill_2": "kutsama.png",
                    "skill_3": "yansitma.png"
                },
                "saman_iyilestirme": {
                    "skill_1": "hiz.png",
                    "skill_2": "yuksek_saldiri.png"
                }
            },
            "skill_1": {"key": "4", "cooldown": 60, "enabled": True},
            "skill_2": {"key": "f2", "cooldown": 180, "enabled": True},
            "skill_3": {"key": "3", "cooldown": 20, "enabled": False},
            "buff_1": {"key": "f3", "cooldown": 200, "enabled": False},
            "buff_2": {"key": "f4", "cooldown": 200, "enabled": False}
        },
        "system": {
            "last_hwnd": 0,
            "last_pid": 0
        },
        "combat": {
            "selection_mode": "Nearest",
            "selection_mode_internal": "nearest",
            "reachable_distance_px": 420,
            "strafe_start_delay": 2,
            "strafe_interval": 1,
            "multi_target_queue_enabled": False,
            "multi_target_queue_size": 3
        },
        "quest": {
            "enabled": False,
            "check_interval": 3
        }
    }

    DEFAULT_CONFIG = {
        "client_1": _CLIENT_TEMPLATE,
        "client_2": {
            **_CLIENT_TEMPLATE,
            "skills": {
                **_CLIENT_TEMPLATE["skills"],
                "active_profile": "saman_ejderha"
            }
        },
        "global": {
            "mouse_id": 11,
            "always_on_top": True,
            "show_all_windows": False,
            "active_config_client": 0,
            "captcha": {
                "enabled": True,
                "api_key": "",
                "selected_model": "gemini-2.5-flash"
            }
        }
    }

    def __init__(self, filename="config.json"):
        self.filename = filename
        self.config = self.load_config()

    def _fresh_defaults(self) -> Dict[str, Any]:
        defaults = copy.deepcopy(self.DEFAULT_CONFIG)
        defaults["client_1"] = copy.deepcopy(defaults.get("client_1", {}))
        defaults["client_2"] = copy.deepcopy(defaults.get("client_2", {}))
        defaults["global"] = copy.deepcopy(defaults.get("global", {}))
        return defaults

    def load_config(self) -> Dict[str, Any]:
        """Loads config from JSON file, creates it if missing."""
        defaults = self._fresh_defaults()

        if not os.path.exists(self.filename):
            self.save_config(defaults)
            return defaults
        
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                return self._merge_defaults(loaded, defaults)
        except Exception as e:
            print(f"Config Load Error: {e}")
            return defaults

    def save_config(self, config: Optional[Dict[str, Any]] = None):
        """Saves current config to JSON file."""
        if config:
            self.config = config
        
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Config Save Error: {e}")

    def get(self, section: str, key: str, default=None):
        """Safe getter for nested config."""
        if "client_1" in self.config and "client_2" in self.config:
            if section == "system" and key == "mouse_id":
                return self.config.get("global", {}).get("mouse_id", default)
            if section == "global":
                return self.config.get("global", {}).get(key, default)
        return self.config.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value):
        """Sets a value and saves."""
        if "client_1" in self.config and "client_2" in self.config:
            if section == "system" and key == "mouse_id":
                self.config.setdefault("global", {})["mouse_id"] = value
                self.save_config()
                return
            if section == "global":
                self.config.setdefault("global", {})[key] = value
                self.save_config()
                return

        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save_config()

    def _merge_defaults(self, loaded, defaults):
        """Recursively merge loaded config with defaults to ensure new keys exist."""
        for k, v in defaults.items():
            if k not in loaded:
                loaded[k] = v
            elif isinstance(v, dict) and isinstance(loaded[k], dict):
                self._merge_defaults(loaded[k], v)
        return loaded
