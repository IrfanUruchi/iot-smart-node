import time
from typing import Any, Dict, List, Tuple

import psutil


class MetricsCollector:
    def __init__(self, docker_manager):
        self.docker_manager = docker_manager

        self.cpu_history: List[float] = []
        self.ram_history: List[float] = []
        self.disk_history: List[float] = []

        self.history_size = 20

    @staticmethod
    def timestamp() -> int:
        return int(time.time())


    def collect(self) -> Dict[str, Any]:
        try:
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            cpu = psutil.cpu_percent(interval=None)
            ram = vm.percent
            disk_percent = disk.percent

            self._update_history(cpu, ram, disk_percent)

            return {
                "timestamp": self.timestamp(),
                "cpu_percent": cpu,
                "ram_percent": ram,
                "ram_available_mb": round(vm.available / 1024 / 1024, 2),
                "disk_percent": disk_percent,
                "running_services": self.docker_manager.list_managed_services(),
            }

        except Exception:
            return {
                "timestamp": self.timestamp(),
                "cpu_percent": 0,
                "ram_percent": 0,
                "ram_available_mb": 0,
                "disk_percent": 0,
                "running_services": [],
            }

    def _update_history(self, cpu: float, ram: float, disk: float) -> None:
        self.cpu_history.append(cpu)
        self.ram_history.append(ram)
        self.disk_history.append(disk)

        if len(self.cpu_history) > self.history_size:
            self.cpu_history.pop(0)
            self.ram_history.pop(0)
            self.disk_history.pop(0)

    def _avg(self, values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0


    def ready_for_deploy(self, limits: Dict[str, Any]) -> Tuple[bool, str]:
        metrics = self.collect()

        cpu_limit = float(limits.get("max_cpu_before_deploy", 85))
        ram_limit = float(limits.get("max_ram_before_deploy", 85))
        disk_limit = float(limits.get("max_disk_before_deploy", 90))

        if metrics["cpu_percent"] >= cpu_limit:
            return False, f"cpu_high:{metrics['cpu_percent']}"

        if metrics["ram_percent"] >= ram_limit:
            return False, f"ram_high:{metrics['ram_percent']}"

        if metrics["disk_percent"] >= disk_limit:
            return False, f"disk_high:{metrics['disk_percent']}"

        return True, "ready"


    def anomaly_alerts(self, limits: Dict[str, Any]) -> List[Dict[str, Any]]:
        metrics = self.collect()
        alerts: List[Dict[str, Any]] = []

        cpu = metrics["cpu_percent"]
        ram = metrics["ram_percent"]
        disk = metrics["disk_percent"]
 

        if cpu >= float(limits.get("anomaly_cpu", 90)):
            alerts.append({
                "type": "high_cpu",
                "severity": "medium",
                "message": f"High CPU usage: {cpu}%",
                "metrics": metrics,
            })

        if ram >= float(limits.get("anomaly_ram", 90)):
            alerts.append({
                "type": "high_ram",
                "severity": "high",
                "message": f"High RAM usage: {ram}%",
                "metrics": metrics,
            })

        if disk >= float(limits.get("anomaly_disk", 95)):
            alerts.append({
                "type": "high_disk",
                "severity": "high",
                "message": f"High disk usage: {disk}%",
                "metrics": metrics,
            })

        if len(self.cpu_history) > 5:
            avg_cpu = self._avg(self.cpu_history)

            if cpu > avg_cpu * 1.5 and cpu > 50:
                alerts.append({
                    "type": "cpu_spike",
                    "severity": "medium",
                    "message": f"CPU spike: {cpu}% (avg {round(avg_cpu,2)}%)",
                    "metrics": metrics,
                })

        if len(self.ram_history) > 5:
            avg_ram = self._avg(self.ram_history)

            if ram > avg_ram * 1.3 and ram > 50:
                alerts.append({
                    "type": "ram_spike",
                    "severity": "high",
                    "message": f"RAM spike: {ram}% (avg {round(avg_ram,2)}%)",
                    "metrics": metrics,
                })

        return alerts