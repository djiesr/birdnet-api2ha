"""Load configuration from config.yaml and environment."""
import os
import yaml

CONFIG_PATH = os.environ.get("BIRDNET_API2HA_CONFIG", "config.yaml")


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(
            f"Config not found: {CONFIG_PATH}. Copy config.yaml.example to config.yaml."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Env overrides
    if os.environ.get("BIRDNET_API2HA_DB"):
        data["database_path"] = os.environ["BIRDNET_API2HA_DB"]
    if os.environ.get("BIRDNET_API2HA_PORT"):
        data["http_port"] = int(os.environ["BIRDNET_API2HA_PORT"])
    return data
