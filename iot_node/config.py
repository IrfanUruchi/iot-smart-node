import copy
import json
import os
import threading
from typing import Any, Dict, List

import yaml

class RuntimeConfig:
    def __init__(self, data: Dict[str, Any]):
        if not isinstance(data, dict):
            raise ValueError("Config must be a dictionary")

        self._data = data
        self._lock = threading.RLock()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, path: str, default: Any = None) -> Any:
        with self._lock:
            current: Any = self._data

            for key in path.split("."):
                if not isinstance(current, dict) or key not in current:
                    return default
                current = current[key]

            return copy.deepcopy(current)

    def set_path(self, path: str, value: Any) -> None:
        if not path or not isinstance(path, str):
            raise ValueError("Invalid config path")

        with self._lock:
            keys = path.split(".")
            current = self._data

            for key in keys[:-1]:
                if key not in current or not isinstance(current[key], dict):
                    current[key] = {}
                current = current[key]

            current[keys[-1]] = value

    def apply_updates(self, updates: Dict[str, Any], allowed_prefixes: List[str]) -> Dict[str, Any]:
        """
        Apply safe runtime config updates.
        Only paths matching allowed_prefixes are accepted.
        """
        applied: Dict[str, Any] = {}

        if not isinstance(updates, dict):
            return applied

        for path, value in updates.items():
    
            if not any(path.startswith(prefix) for prefix in allowed_prefixes):
                continue

            if value is None:
                continue

            if len(str(value)) > 10000:  
                continue

            try:
                self.set_path(path, value)
                applied[path] = value
            except Exception:
                continue

        return applied


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_config() -> RuntimeConfig:
    config_path = os.getenv("CONFIG_PATH", "/app/config.yaml")

    if not os.path.exists(config_path):
        config_path = "config.yaml"

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    if not isinstance(config, dict):
        raise ValueError("Invalid YAML config format")


    if os.getenv("NODE_ID"):
        config.setdefault("device", {})["id"] = os.getenv("NODE_ID")

    if os.getenv("MQTT_HOST"):
        config.setdefault("mqtt", {})["host"] = os.getenv("MQTT_HOST")

    if os.getenv("MQTT_PORT"):
        config.setdefault("mqtt", {})["port"] = _env_int("MQTT_PORT", 1883)

    if os.getenv("MQTT_USE_TLS") is not None:
        config.setdefault("mqtt", {})["use_tls"] = _env_bool("MQTT_USE_TLS", False)

    if os.getenv("DEVICE_TOKEN"):
        config.setdefault("security", {})["token"] = os.getenv("DEVICE_TOKEN")

    if os.getenv("ALLOWED_SERVICES"):
        try:
            services = json.loads(os.getenv("ALLOWED_SERVICES", "{}"))
            if isinstance(services, dict):
                config["services"] = services
        except json.JSONDecodeError:
            pass

    return RuntimeConfig(config)