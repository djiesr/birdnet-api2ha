"""
Lecture de la config BirdNET-Go pour afficher le type de base (SQLite/MySQL) et le chemin/nom.
"""
import os
from pathlib import Path
from typing import Any

import yaml

# Dossiers typiques pour chercher la config BirdNET-Go
SEARCH_DIRS = [
    Path.home() / "birdnet-go-app",
    Path.home() / "BirdNET-Go",
    Path.cwd(),
]
CONFIG_NAMES = ["config.yaml", "config.yml"]


def find_birdnet_config_path(database_path: str | None = None) -> Path | None:
    """
    Trouve le fichier config BirdNET-Go.
    Si database_path est fourni (ex. .../data/birdnet.db), cherche dans le parent et config/.
    Sinon cherche dans SEARCH_DIRS.
    """
    if database_path:
        db = Path(database_path).resolve()
        if db.is_file():
            # .../birdnet-go-app/data/birdnet.db -> .../birdnet-go-app/config/config.yaml
            for parent in [db.parent, db.parent.parent]:
                for name in CONFIG_NAMES:
                    for sub in [parent, parent / "config"]:
                        cfg = sub / name
                        if cfg.is_file():
                            return cfg
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for name in CONFIG_NAMES:
            for base in [d, d / "config"]:
                cfg = base / name
                if cfg.is_file():
                    return cfg
    return None


def load_birdnet_config(config_path: Path) -> dict[str, Any]:
    """Charge le YAML BirdNET-Go."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_database_info(config_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """
    Extrait type de base, chemin/nom depuis la config BirdNET-Go.
    Retourne: database_type, sqlite_path (absolu si possible), mysql_* (si mysql).
    """
    config_dir = config_path.parent
    # Cas config dans .../config/config.yaml -> racine = parent du dossier config
    if config_dir.name == "config":
        app_root = config_dir.parent
    else:
        app_root = config_dir

    out = data.get("output") or {}
    sqlite_cfg = out.get("sqlite") or {}
    mysql_cfg = out.get("mysql") or {}

    sqlite_enabled = sqlite_cfg.get("enabled", False)
    mysql_enabled = mysql_cfg.get("enabled", False)

    result = {
        "database_type": "unknown",
        "config_path": str(config_path.resolve()),
        "sqlite": {
            "enabled": sqlite_enabled,
            "path": None,
            "path_resolved": None,
        },
        "mysql": {
            "enabled": mysql_enabled,
            "host": mysql_cfg.get("host"),
            "port": mysql_cfg.get("port"),
            "database": mysql_cfg.get("database"),
            "username": mysql_cfg.get("username"),
        },
    }

    if sqlite_enabled:
        path = sqlite_cfg.get("path")
        result["sqlite"]["path"] = path
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = app_root / p
            if not p.is_file():
                # Essayer data/birdnet.db
                alt = app_root / "data" / Path(path).name
                if alt.is_file():
                    p = alt
            result["sqlite"]["path_resolved"] = str(p) if p.is_file() else None
        result["database_type"] = "sqlite"

    if mysql_enabled:
        result["database_type"] = "mysql"
        # Ne pas exposer le mot de passe
        result["mysql"]["host"] = mysql_cfg.get("host", "localhost")
        result["mysql"]["port"] = mysql_cfg.get("port", 3306)
        result["mysql"]["database"] = mysql_cfg.get("database", "birdnet")

    return result


def get_birdnet_config_info(database_path: str | None = None, config_path_override: str | None = None) -> dict[str, Any] | None:
    """
    Point d'entrée: trouve la config BirdNET-Go, la charge et retourne les infos base.
    Si config_path_override est fourni, l'utilise en priorité.
    Retourne None si aucune config trouvée.
    """
    path = None
    if config_path_override and os.path.isfile(config_path_override):
        path = Path(config_path_override)
    if path is None:
        path = find_birdnet_config_path(database_path)
    if path is None:
        return None
    data = load_birdnet_config(path)
    return get_database_info(path, data)
