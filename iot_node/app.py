import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from .config import RuntimeConfig, load_config
from .docker_manager import DockerManager
from .logging_setup import setup_logging
from .metrics import MetricsCollector
from .mqtt_bus import MqttBus
from .security import RateLimiter, validate_command, verify_token


class IoTEdgeNode:
    def __init__(self):
        self.log = setup_logging()
        self.cfg: RuntimeConfig = load_config()

        self.node_id = self.cfg.get("device.id", "node-unknown")
        self.shutdown = threading.Event()

        self.docker = DockerManager(self.node_id, self.log)
        self.metrics = MetricsCollector(self.docker)
        self.mqtt = MqttBus(self.cfg, self.node_id, self.log)

        self.executor = ThreadPoolExecutor(max_workers=4)

        self.rate_limiter = RateLimiter(
            rate_per_minute=int(self.cfg.get("security.rate_limit_per_minute", 20)),
            burst_limit=int(self.cfg.get("security.burst_limit", 5)),
            burst_window_seconds=int(self.cfg.get("security.burst_window_seconds", 5)),
        )

        self.last_deploy_ts = 0
        self.deploy_cooldown = int(self.cfg.get("limits.deploy_cooldown_seconds", 3))
        self.max_containers = int(self.cfg.get("limits.max_containers", 5))

    def ts(self) -> int:
        return int(time.time())

    def status(self, event: str, extra: Dict[str, Any] | None = None) -> None:
        metrics = self.metrics.collect()

        payload = {
            "event": event,
            "node_id": self.node_id,
            "timestamp": self.ts(),
            "metrics": metrics,
            "running_services": metrics.get("running_services", []),
        }

        if extra:
            payload.update(extra)

        self.mqtt.publish("status", payload)

    def alert(self, alert_type: str, severity: str, message: str, extra: Dict[str, Any] | None = None) -> None:
        payload = {
            "event": "ids_alert",
            "node_id": self.node_id,
            "timestamp": self.ts(),
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
        }

        if extra:
            payload.update(extra)

        self.mqtt.publish("alerts", payload)
        self.log.warning("IDS alert type=%s severity=%s message=%s", alert_type, severity, message)

    def pre_pull_images(self) -> None:
        if not self.cfg.get("docker.pull_on_startup", False):
            return

        services = self.cfg.get("services", {}) or {}

        if not services:
            self.log.info("No services configured for pre-pull")
            return

        self.log.info("Pre-pull enabled for %s services", len(services))

        for service_name, service_cfg in services.items():
            image = service_cfg.get("image")

            if not image:
                self.log.warning("Skipping pre-pull for service=%s because image is missing", service_name)
                continue

            try:
                self.log.info("Pre-pulling service=%s image=%s", service_name, image)
                self.docker.client.images.pull(image)
                self.log.info("Pre-pull completed service=%s image=%s", service_name, image)
            except Exception:
                self.log.exception("Failed to pre-pull service=%s image=%s", service_name, image)

    def heartbeat_loop(self) -> None:
        while not self.shutdown.is_set():
            try:
                docker_cfg = self.cfg.get("docker", {})
                limits = self.cfg.get("limits", {})

                if docker_cfg.get("self_heal", True):
                    restarted = self.docker.self_heal()
                    for name in restarted:
                        self.alert("self_heal", "low", f"Restarted managed container {name}")

                for alert in self.metrics.anomaly_alerts(limits):
                    self.alert(alert["type"], alert["severity"], alert["message"], {"metrics": alert["metrics"]})

                self.mqtt.publish("heartbeat", {
                    "event": "heartbeat",
                    "node_id": self.node_id,
                    "timestamp": self.ts(),
                    "metrics": self.metrics.collect(),
                    "running_services": self.docker.list_managed_services(),
                    "allowed_services": list((self.cfg.get("services", {}) or {}).keys()),
                    "limits": limits,
                })

            except Exception:
                self.log.exception("Heartbeat loop failed")

            interval = int(self.cfg.get("heartbeat.interval", 10))
            self.shutdown.wait(interval)

    def handle_mqtt_message(self, payload: Dict[str, Any], topic: str) -> None:
        if payload.get("action") == "_invalid_json":
            self.alert("invalid_json", "medium", "Received invalid JSON", {"topic": topic})
            return

        if self.rate_limiter.limited():
            self.alert("rate_limit", "medium", "Command rate limit triggered", {"topic": topic})
            return

        self.executor.submit(self.handle_command, payload, topic)

    def handle_command(self, payload: Dict[str, Any], topic: str) -> None:
        token = self.cfg.get("security.token", "")

        if not verify_token(payload.get("token"), token):
            self.alert("invalid_token", "high", "Command rejected because token validation failed", {"topic": topic})
            return

        valid, reason = validate_command(payload, self.node_id)
        if not valid:
            if reason != "wrong_node":
                self.alert("invalid_command", "medium", reason, {"topic": topic})
            return

        action = payload.get("action")
        self.log.info("Command accepted action=%s topic=%s", action, topic)

        try:
            if action == "deploy":
                self.deploy(payload)
            elif action == "stop":
                self.stop(payload)
            elif action == "status":
                self.status("status_response")
            elif action == "config_update":
                self.update_config(payload)
        except Exception:
            self.log.exception("Command execution failed action=%s", action)

    def deploy(self, payload: Dict[str, Any]) -> None:
        service_name = payload.get("service")
        services = self.cfg.get("services", {})

        if service_name not in services:
            self.alert("unauthorized_service", "high", f"Service not allowed: {service_name}")
            self.status("deploy_result", {"success": False, "reason": "service_not_allowed"})
            return

        now = time.time()

        if now - self.last_deploy_ts < self.deploy_cooldown:
            self.alert("deploy_throttle", "medium", "Deploy too frequent")
            return

        if len(self.docker.list_managed_services()) >= self.max_containers:
            self.status("deploy_result", {
                "success": False,
                "reason": "max_containers_reached",
            })
            return

        ready, reason = self.metrics.ready_for_deploy(self.cfg.get("limits", {}))

        if not ready:
            self.status("deploy_result", {
                "success": False,
                "reason": "node_not_ready",
                "details": reason,
            })
            return

        requested_name = payload.get("name") or f"{self.node_id}-{service_name}"

        ok, result = self.docker.deploy(
            service_name=service_name,
            service_cfg=services[service_name],
            requested_name=requested_name,
            docker_cfg=self.cfg.get("docker", {}),
        )

        if ok:
            self.last_deploy_ts = now

        self.status("deploy_result", {
            "success": ok,
            "service": service_name,
            **result,
        })

    def stop(self, payload: Dict[str, Any]) -> None:
        ok, result = self.docker.stop(
            container_name=payload.get("name", ""),
            timeout=int(self.cfg.get("docker.stop_timeout_seconds", 10)),
        )

        if not ok and result.get("reason") == "unmanaged_container":
            self.alert("unauthorized_stop", "high", "Refused to stop unmanaged container")

        self.status("stop_result", {"success": ok, **result})

    def update_config(self, payload: Dict[str, Any]) -> None:
        if not self.cfg.get("security.allow_runtime_config_updates", True):
            self.status("config_update_result", {
                "success": False,
                "reason": "runtime_config_disabled",
            })
            return

        updates = payload.get("updates", {})
        allowed_prefixes = self.cfg.get("security.allowed_runtime_update_prefixes", [])

        applied = self.cfg.apply_updates(updates, allowed_prefixes)
        blocked = {k: v for k, v in updates.items() if k not in applied}

        if applied:
            self.log.info("Config updated: %s", applied)
            self.alert("config_update", "low", f"Updated: {list(applied.keys())}")

        self.status("config_update_result", {
            "success": bool(applied),
            "applied": applied,
            "blocked": blocked,
        })

    def start(self) -> None:
        self.log.info("Starting smart IoT node id=%s", self.node_id)
        self.log.info("Configured services=%s", list((self.cfg.get("services", {}) or {}).keys()))

        self.pre_pull_images()

        self.mqtt.on_command = self.handle_mqtt_message
        self.mqtt.connect()
        self.mqtt.start()

        self.status("node_connected")

        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

        while not self.shutdown.is_set():
            self.shutdown.wait(1)

    def stop_node(self, signum=None, frame=None) -> None:
        self.log.info("Stopping node signum=%s", signum)
        self.shutdown.set()

        try:
            self.status("node_shutdown")
        except Exception:
            self.log.exception("Failed to publish shutdown status")

        self.mqtt.stop()
        self.executor.shutdown(wait=False)


def main() -> None:
    node = IoTEdgeNode()

    signal.signal(signal.SIGTERM, node.stop_node)
    signal.signal(signal.SIGINT, node.stop_node)

    node.start()