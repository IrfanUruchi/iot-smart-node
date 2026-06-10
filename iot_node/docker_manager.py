import os
import re
from typing import Any, Dict, List, Tuple

import docker


_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def sanitize_name(name: str) -> str:
    return _NAME_RE.sub("", str(name))[:80]


class DockerManager:
    def __init__(self, node_id: str, logger):
        self.node_id = node_id
        self.log = logger
        self.client = docker.from_env()

    def list_managed_services(self) -> List[Dict[str, Any]]:
        services = []

        try:
            for container in self.client.containers.list(all=False):
                labels = container.labels or {}

                if labels.get("iot.node_id") == self.node_id and labels.get("iot.managed") == "true":
                    services.append({
                        "name": container.name,
                        "service": labels.get("iot.service", "unknown"),
                        "image": container.image.tags[0] if container.image.tags else "unknown",
                        "status": container.status,
                    })
        except Exception:
            self.log.exception("Failed to list managed services")

        return services

    def self_heal(self) -> List[str]:
        restarted = []

        try:
            for container in self.client.containers.list(all=True):
                labels = container.labels or {}

                if labels.get("iot.node_id") == self.node_id and labels.get("iot.managed") == "true":
                    if container.status not in {"running"}:
                        self.log.warning("Self-healing container=%s status=%s", container.name, container.status)
                        container.restart()
                        restarted.append(container.name)
        except Exception:
            self.log.exception("Self-heal failed")

        return restarted

    def _ports(self, ports: List[str]) -> Dict[str, int]:
        result = {}

        for item in ports:
            try:
                host, container = item.split(":", 1)
                result[f"{container}/tcp"] = int(host)
            except Exception:
                self.log.warning("Invalid port mapping ignored: %s", item)

        return result

    def _env(self, env_vars: List[str]) -> Dict[str, str]:
        result = {}

        for item in env_vars:
            try:
                key, value = item.split("=", 1)

                if value.startswith("${") and value.endswith("}"):
                    env_name = value[2:-1]
                    value = os.getenv(env_name, "")

                result[key] = value

            except Exception:
                self.log.warning("Invalid env var ignored: %s", item)

        return result

    def _volumes(self, volumes: List[str]) -> Dict[str, Dict[str, str]]:
        result = {}

        for item in volumes:
            try:
                host_path, container_path = item.split(":", 1)
                result[host_path] = {"bind": container_path, "mode": "rw"}
            except Exception:
                self.log.warning("Invalid volume mapping ignored: %s", item)

        return result

    def deploy(
        self,
        service_name: str,
        service_cfg: Dict[str, Any],
        requested_name: str,
        docker_cfg: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any]]:

        image = service_cfg.get("image")

        if not image:
            return False, {"reason": "missing_image"}

        container_name = sanitize_name(requested_name)

        if not container_name:
            return False, {"reason": "invalid_container_name"}

        try:
            existing = self.client.containers.list(all=True, filters={"name": container_name})

            if existing:
                return False, {
                    "reason": "container_already_exists",
                    "container": container_name,
                }

            if docker_cfg.get("pull_before_run", True):
                self.log.info("Pulling image=%s", image)
                self.client.images.pull(image)

            kwargs = {
                "image": image,
                "name": container_name,
                "detach": True,
                "ports": self._ports(service_cfg.get("ports", [])),
                "environment": self._env(service_cfg.get("env", [])),
                "volumes": self._volumes(service_cfg.get("volumes", [])),
                "network_mode": docker_cfg.get("network_mode", "bridge"),
                "restart_policy": {
                    "Name": docker_cfg.get("restart_policy", "unless-stopped")
                },
                "labels": {
                    "iot.node_id": self.node_id,
                    "iot.service": service_name,
                    "iot.managed": "true",
                },
                "read_only": bool(service_cfg.get("read_only", False)),
                "tmpfs": service_cfg.get("tmpfs", []),
                "security_opt": ["no-new-privileges"],
                "pids_limit": 100,
            }

            if service_cfg.get("cpu_limit"):
                kwargs["nano_cpus"] = int(float(service_cfg["cpu_limit"]) * 1_000_000_000)

            if service_cfg.get("mem_limit"):
                kwargs["mem_limit"] = str(service_cfg["mem_limit"])

            if service_cfg.get("memswap_limit"):
                kwargs["memswap_limit"] = str(service_cfg["memswap_limit"])

            if service_cfg.get("shm_size"):
                kwargs["shm_size"] = str(service_cfg["shm_size"])

            self.log.info(
                "Starting container service=%s name=%s image=%s",
                service_name,
                container_name,
                image,
            )

            container = self.client.containers.run(**kwargs)

            container.reload()

            if container.status != "running":
                return False, {
                    "reason": "container_failed_to_start",
                    "container": container_name,
                }

            return True, {
                "container": container.name,
                "image": image,
                "service": service_name,
            }

        except docker.errors.ImageNotFound:
            return False, {
                "reason": "image_not_found",
                "image": image,
            }

        except docker.errors.APIError as exc:
            return False, {
                "reason": "docker_api_error",
                "error": str(exc),
            }

        except Exception as exc:
            self.log.exception("Unexpected deploy failure")
            return False, {
                "reason": "unexpected_error",
                "error": str(exc),
            }

    def stop(self, container_name: str, timeout: int) -> Tuple[bool, Dict[str, Any]]:
        container_name = sanitize_name(container_name)

        if not container_name:
            return False, {"reason": "invalid_container_name"}

        try:
            container = self.client.containers.get(container_name)
            labels = container.labels or {}

            if labels.get("iot.node_id") != self.node_id or labels.get("iot.managed") != "true":
                return False, {
                    "reason": "unmanaged_container",
                    "container": container_name,
                }

            container.stop(timeout=timeout)
            container.remove()

            return True, {
                "container": container_name,
            }

        except docker.errors.NotFound:
            return False, {
                "reason": "container_not_found",
                "container": container_name,
            }

        except Exception as exc:
            self.log.exception("Stop failed")
            return False, {
                "reason": "stop_failed",
                "error": str(exc),
            }