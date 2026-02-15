"""
MQTT bridge: poll BirdNET-Go DB for new detections and publish each to a topic.
Home Assistant can subscribe to birdnet_api2ha/detections without modifying BirdNET-Go.
"""
import json
import os
import time
from typing import Any, Optional

import paho.mqtt.client as mqtt

from config import load_config
from db import get_connection, get_detections_v2, get_max_detection_id

_last_max_id: int = 0


def _publish_detection(client: mqtt.Client, topic: str, det: dict[str, Any]) -> None:
    payload = {
        "id": det.get("id"),
        "timestamp": det.get("timestamp"),
        "common_name": det.get("common_name"),
        "scientific_name": det.get("scientific_name"),
        "confidence": det.get("confidence"),
    }
    client.publish(topic, json.dumps(payload), qos=0, retain=False)


def run_bridge():
    cfg = load_config()
    mqtt_cfg = cfg.get("mqtt") or {}
    if not mqtt_cfg.get("enabled"):
        return
    db_path = cfg.get("database_path")
    if not db_path or not os.path.isfile(db_path):
        return
    host = mqtt_cfg.get("host", "localhost")
    port = int(mqtt_cfg.get("port", 1883))
    topic = mqtt_cfg.get("topic", "birdnet_api2ha/detections")
    interval = int(mqtt_cfg.get("poll_interval_seconds", 10))
    username = mqtt_cfg.get("username") or os.environ.get("BIRDNET_MQTT_USERNAME", "")
    password = mqtt_cfg.get("password") or os.environ.get("BIRDNET_MQTT_PASSWORD", "")

    client = mqtt.Client(client_id="birdnet-api2ha")
    if username:
        client.username_pw_set(username, password)
    client.connect(host, port, 60)
    client.loop_start()

    global _last_max_id
    with get_connection(db_path) as conn:
        _last_max_id = get_max_detection_id(conn)

    try:
        while True:
            time.sleep(interval)
            with get_connection(db_path) as conn:
                current_max = get_max_detection_id(conn)
                if current_max <= _last_max_id:
                    continue
                items = get_detections_v2(conn, limit=500, after_id=_last_max_id)
                for det in items:
                    _publish_detection(client, topic, det)
                _last_max_id = current_max
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
