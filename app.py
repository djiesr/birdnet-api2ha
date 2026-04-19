"""
Flask API: GET /api/detections, GET /api/stats, GET /api/system.
Same JSON contract as BirdNET-Go Home Assistant API.
"""
import os
import socket
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import psutil
from flask import Flask, jsonify, request

from config import load_config
from db import get_connection, get_detections_v2, get_stats_v2, get_hourly_detections, get_aggregate_detections, SchemaError
from birdnet_config import get_birdnet_config_info

app = Flask(__name__)
_config: Optional[dict] = None


def get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _parse_date_range(period: Optional[str], date_start: Optional[str], date_end: Optional[str]):
    """
    Si period=week : semaine courante (lundi à aujourd'hui).
    Sinon retourne date_start et date_end tels quels.
    """
    if (period or "").strip().lower() != "week":
        return date_start, date_end
    today = datetime.now().date()
    # Lundi = premier jour de la semaine (isoweekday: 1=lundi, 7=dimanche)
    days_since_monday = today.isoweekday() - 1
    monday = today - timedelta(days=days_since_monday)
    return monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


@app.route("/")
def index():
    base = request.host_url.rstrip("/")
    payload = {
        "service": "birdnet-api2ha",
        "description": "Pont BirdNET-Go vers Home Assistant (API REST + MQTT)",
        "endpoints": {
            "health": f"{base}/health",
            "detections": f"{base}/api/detections",
            "stats": f"{base}/api/stats",
            "birdnet_config": f"{base}/api/birdnet-config",
        },
        "params": {
            "detections": "date_start, date_end, period=week (semaine courante), common_name, limit",
            "stats": "date_start, date_end, period=week (semaine courante)",
        },
    }
    cfg = get_config()
    birdnet_info = get_birdnet_config_info(
        cfg.get("database_path") or None,
        (cfg.get("birdnet_config_path") or "").strip() or None,
    )
    if birdnet_info:
        payload["database"] = {
            "type": birdnet_info["database_type"],
            "from_birdnet_config": True,
        }
        if birdnet_info["database_type"] == "sqlite" and birdnet_info["sqlite"].get("path_resolved"):
            payload["database"]["path"] = birdnet_info["sqlite"]["path_resolved"]
        elif birdnet_info["database_type"] == "mysql":
            payload["database"]["mysql"] = {
                "host": birdnet_info["mysql"].get("host"),
                "port": birdnet_info["mysql"].get("port"),
                "database": birdnet_info["mysql"].get("database"),
            }
    return jsonify(payload)


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "birdnet-api2ha"})


@app.route("/api/birdnet-config")
def api_birdnet_config():
    """Infos lues depuis la config BirdNET-Go : type de base (SQLite/MySQL) et chemin/nom."""
    cfg = get_config()
    info = get_birdnet_config_info(
        cfg.get("database_path") or None,
        (cfg.get("birdnet_config_path") or "").strip() or None,
    )
    if info is None:
        return jsonify({
            "found": False,
            "message": "Config BirdNET-Go non trouvée. Indiquez birdnet_config_path dans config.yaml ou placez la DB dans un dossier connu.",
        }), 200
    out = {
        "found": True,
        "database_type": info["database_type"],
        "config_path": info["config_path"],
        "sqlite": info["sqlite"],
        "mysql": {k: v for k, v in info["mysql"].items() if k != "username"},
    }
    if info["database_type"] == "mysql":
        out["note"] = "MySQL détecté : lecture non supportée pour l'instant, seul SQLite est pris en charge."
    return jsonify(out)


@app.route("/api/detections")
def api_detections():
    cfg = get_config()
    db_path = (cfg.get("database_path") or "").strip()
    if not db_path or not os.path.isfile(db_path):
        return jsonify([]), 200
    period = request.args.get("period") or None
    date_start, date_end = _parse_date_range(
        period,
        request.args.get("date_start") or None,
        request.args.get("date_end") or None,
    )
    common_name = request.args.get("common_name") or None
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    base_url = request.host_url.rstrip("/")
    clips_base = cfg.get("clips_base_path") or ""
    try:
        with get_connection(db_path) as conn:
            items = get_detections_v2(
                conn, date_start=date_start, date_end=date_end, common_name=common_name, limit=limit
            )
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500
    except SchemaError as e:
        return jsonify({"error": str(e)}), 500
    except sqlite3.OperationalError as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    for it in items:
        it["audio_url"] = ""
        if it.get("audio_path") and base_url and clips_base:
            it["audio_url"] = f"{base_url}/api/audio?id={it['id']}"
    return jsonify(items)


@app.route("/api/stats")
def api_stats():
    cfg = get_config()
    db_path = (cfg.get("database_path") or "").strip()
    if not db_path or not os.path.isfile(db_path):
        return jsonify([]), 200
    period = request.args.get("period") or None
    date_start, date_end = _parse_date_range(
        period,
        request.args.get("date_start") or None,
        request.args.get("date_end") or None,
    )
    try:
        with get_connection(db_path) as conn:
            items = get_stats_v2(conn, date_start=date_start, date_end=date_end)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500
    except SchemaError as e:
        return jsonify({"error": str(e)}), 500
    except sqlite3.OperationalError as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    return jsonify(items)


@app.route("/api/hourly")
def api_hourly():
    """Détections par espèce par heure pour une date donnée."""
    cfg = get_config()
    db_path = (cfg.get("database_path") or "").strip()
    if not db_path or not os.path.isfile(db_path):
        return jsonify({"date": "", "sunrise": None, "sunset": None, "species": []}), 200
    date_str = (request.args.get("date") or "").strip()
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with get_connection(db_path) as conn:
            data = get_hourly_detections(conn, date_str)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/api/aggregate")
def api_aggregate():
    """Détections agrégées par jour/semaine/mois. ?mode=daily|weekly|monthly"""
    cfg = get_config()
    db_path = (cfg.get("database_path") or "").strip()
    if not db_path or not os.path.isfile(db_path):
        return jsonify({"mode": "", "columns": [], "species": []}), 200
    mode = request.args.get("mode", "daily").strip().lower()
    if mode not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "mode doit être daily, weekly ou monthly"}), 400
    try:
        with get_connection(db_path) as conn:
            data = get_aggregate_detections(conn, mode)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(data)


@app.route("/api/system")
def api_system():
    """Métriques système du serveur : IP, CPU, RAM, disque, uptime."""
    try:
        # IP de l'interface réseau sortante (sans envoyer de paquets)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
    except Exception:
        ip_address = "unknown"

    uptime_seconds = int(time.time() - psutil.boot_time())
    disk = psutil.disk_usage("/")

    return jsonify({
        "ip_address": ip_address,
        "uptime_seconds": uptime_seconds,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": disk.percent,
    })


def run_app():
    cfg = load_config()
    host = cfg.get("http_host", "0.0.0.0")
    port = int(cfg.get("http_port", 8081))
    # Vérifier que les routes sont bien enregistrées (éviter 404 si ancien process)
    rules = [r.rule for r in app.url_map.iter_rules() if not r.rule.startswith("/static")]
    print(f"Routes: {rules}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_app()
