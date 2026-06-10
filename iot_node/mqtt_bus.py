import json
import ssl
import time
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt

class MqttBus:
    def __init__(self, cfg, node_id: str, logger):
        self.cfg = cfg
        self.node_id = node_id
        self.log = logger
        self.client: Optional[mqtt.Client] = None
        self.on_command: Optional[Callable[[Dict[str, Any], str], None]] = None

    def topic(self, name: str) -> str:
        value = self.cfg.get(f"topics.{name}")
        if isinstance(value, str):
            return value.format(node_id=self.node_id)
        raise ValueError(f"Missing topic: {name}")


    def publish(self, topic_name: str, payload: Dict[str, Any], qos: int = 1, retain: bool = False) -> None:
        if self.client is None:
            self.log.warning("MQTT not ready; dropping topic=%s", topic_name)
            return

        topic = self.topic(topic_name)

        try:
            result = self.client.publish(
                topic,
                json.dumps(payload, separators=(",", ":")),
                qos=qos,
                retain=retain,
            )

            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                self.log.warning("MQTT publish failed rc=%s topic=%s", result.rc, topic)

        except Exception:
            self.log.exception("Failed to publish topic=%s", topic)

    def publish_raw(self, topic: str, payload: Dict[str, Any], qos: int = 1, retain: bool = False) -> None:
        if self.client is None:
            return

        try:
            self.client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=qos, retain=retain)
        except Exception:
            self.log.exception("Failed to publish raw topic=%s", topic)


    def connect(self) -> None:
        mqtt_cfg = self.cfg.get("mqtt", {})
        host = mqtt_cfg.get("host", "localhost")
        port = int(mqtt_cfg.get("port", 1883))
        keepalive = int(mqtt_cfg.get("keepalive", 60))

        self.client = mqtt.Client(client_id=self.node_id, clean_session=False)

        self.client.will_set(
            self.topic("status"),
            json.dumps({
                "event": "node_disconnected",
                "node_id": self.node_id,
                "timestamp": int(time.time())
            }),
            qos=1,
            retain=False,
        )

        if mqtt_cfg.get("use_tls", False):
            ca_cert = mqtt_cfg.get("ca_cert") or None
            client_cert = mqtt_cfg.get("client_cert") or None
            client_key = mqtt_cfg.get("client_key") or None

            self.client.tls_set(
                ca_certs=ca_cert,
                certfile=client_cert,
                keyfile=client_key,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            self.client.tls_insecure_set(False)

        self.client.reconnect_delay_set(min_delay=2, max_delay=30)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        while True:
            try:
                self.log.info(
                    "Connecting MQTT host=%s port=%s tls=%s",
                    host,
                    port,
                    mqtt_cfg.get("use_tls", False),
                )

                self.client.connect(host, port, keepalive)
                return

            except Exception:
                self.log.exception("MQTT connection failed; retrying")
                time.sleep(5)


    def start(self) -> None:
        if self.client is None:
            raise RuntimeError("MQTT client not connected")

        self.client.loop_start()

    def stop(self) -> None:
        if self.client is None:
            return

        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            self.log.exception("Failed to stop MQTT client")

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.log.error("MQTT connection failed rc=%s", rc)
            return

        command_topic = self.topic("command")
        broadcast_topic = self.topic("broadcast")

        client.subscribe(command_topic, qos=1)
        client.subscribe(broadcast_topic, qos=1)

        self.log.info(
            "MQTT connected. subscribed=%s,%s",
            command_topic,
            broadcast_topic,
        )

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            self.log.warning("Unexpected MQTT disconnect rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            self.log.warning("Invalid JSON received topic=%s", msg.topic)

            if self.on_command:
                self.on_command({"action": "_invalid_json"}, msg.topic)
            return

        if self.on_command:
            try:
                self.on_command(payload, msg.topic)
            except Exception:
                self.log.exception("Error handling command")