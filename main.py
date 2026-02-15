#!/usr/bin/env python3
"""
BirdNET-Go API to Home Assistant.
Run: python main.py          (API only)
     python main.py --mqtt   (API + MQTT bridge in background thread)
"""
import argparse
import threading

from app import app, get_config, run_app
from mqtt_bridge import run_bridge


def main():
    parser = argparse.ArgumentParser(description="BirdNET-Go API to Home Assistant")
    parser.add_argument("--mqtt", action="store_true", help="Run MQTT bridge (publish new detections)")
    args = parser.parse_args()

    get_config()  # Load once, fail fast if config missing

    if args.mqtt:
        t = threading.Thread(target=run_bridge, daemon=True)
        t.start()
    run_app()


if __name__ == "__main__":
    main()
