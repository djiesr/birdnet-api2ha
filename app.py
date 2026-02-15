"""
Flask API: GET /api/detections, GET /api/stats.
Same JSON contract as BirdNET-Go Home Assistant API.
"""
import os
from typing import Optional

from flask import Flask, jsonify, request

from config import load_config
from db import get_connection, get_detections_v2, get_stats_v2

app = Flask(__name__)
_config: Optional[dict] = None


def get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "birdnet-api2ha"})


@app.route("/api/detections")
def api_detections():
    cfg = get_config()
    db_path = cfg.get("database_path")
    if not db_path or not os.path.isfile(db_path):
        return jsonify([]), 200
    date_start = request.args.get("date_start") or None
    date_end = request.args.get("date_end") or None
    common_name = request.args.get("common_name") or None
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    base_url = request.host_url.rstrip("/")
    clips_base = cfg.get("clips_base_path") or ""

    with get_connection(db_path) as conn:
        items = get_detections_v2(
            conn, date_start=date_start, date_end=date_end, common_name=common_name, limit=limit
        )
    for it in items:
        it["audio_url"] = ""
        if it.get("audio_path") and base_url and clips_base:
            # Optional: serve clip or link to BirdNET-Go media endpoint
            it["audio_url"] = f"{base_url}/api/audio?id={it['id']}"
    return jsonify(items)


@app.route("/api/stats")
def api_stats():
    cfg = get_config()
    db_path = cfg.get("database_path")
    if not db_path or not os.path.isfile(db_path):
        return jsonify([]), 200
    date_start = request.args.get("date_start") or None
    date_end = request.args.get("date_end") or None
    with get_connection(db_path) as conn:
        items = get_stats_v2(conn, date_start=date_start, date_end=date_end)
    return jsonify(items)


def run_app():
    cfg = load_config()
    host = cfg.get("http_host", "0.0.0.0")
    port = int(cfg.get("http_port", 8081))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_app()
