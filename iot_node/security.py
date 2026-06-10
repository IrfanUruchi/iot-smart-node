import hmac
import time
import threading
from collections import deque
from typing import Any, Dict, Optional, Tuple


class RateLimiter:
    """
    Thread-safe sliding window + burst limiter.
    Uses monotonic clock to avoid system time issues.
    """

    def __init__(self, rate_per_minute: int, burst_limit: int, burst_window_seconds: int):
        self.rate_per_minute = max(1, int(rate_per_minute))
        self.burst_limit = max(1, int(burst_limit))
        self.burst_window_seconds = max(1, int(burst_window_seconds))

        self._events = deque()
        self._lock = threading.Lock()

    def limited(self) -> bool:
        now = time.monotonic()

        with self._lock:
            # Clean old events (1 minute window)
            while self._events and now - self._events[0] > 60:
                self._events.popleft()

            # Hard rate limit
            if len(self._events) >= self.rate_per_minute:
                return True

            # Burst limit (short window)
            recent_count = 0
            for t in reversed(self._events):
                if now - t <= self.burst_window_seconds:
                    recent_count += 1
                else:
                    break

            if recent_count >= self.burst_limit:
                return True

            self._events.append(now)
            return False


def verify_token(candidate: Optional[str], expected: str) -> bool:
    """
    Constant-time comparison to prevent timing attacks.
    """
    if not candidate or not expected:
        return False

    try:
        return hmac.compare_digest(str(candidate), str(expected))
    except Exception:
        return False

def validate_command(payload: Dict[str, Any], node_id: str) -> Tuple[bool, str]:
    """
    Validate incoming MQTT command payload.
    Returns (is_valid, reason)
    """

    if not isinstance(payload, dict):
        return False, "payload_not_object"

    if len(str(payload)) > 5000:
        return False, "payload_too_large"

    action = payload.get("action")

    if not isinstance(action, str):
        return False, "invalid_action_type"

    allowed_actions = {"deploy", "stop", "status", "config_update"}

    if action not in allowed_actions:
        return False, "unsupported_action"

    target = payload.get("node_id")

    if target is not None:
        if not isinstance(target, str):
            return False, "invalid_node_id_type"

        if target != node_id:
            return False, "wrong_node"

    if action == "deploy":
        service = payload.get("service")

        if not isinstance(service, str) or not service:
            return False, "missing_or_invalid_service"

        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            return False, "invalid_container_name_type"

    elif action == "stop":
        name = payload.get("name")

        if not isinstance(name, str) or not name:
            return False, "missing_container_name"

    elif action == "config_update":
        updates = payload.get("updates")

        if not isinstance(updates, dict):
            return False, "missing_updates"

        if len(updates) > 50:
            return False, "too_many_updates"

    return True, "ok"