#!/usr/bin/env python3
"""
Configuration interactive ou automatique pour birdnet-api2ha.
Recherche la base BirdNET-Go et la config, pose des questions si besoin, écrit config.yaml.
Option: configurer le démarrage automatique au boot (fichier systemd).
Usage: python configure.py [--non-interactive]
"""
import argparse
import getpass
import subprocess
import sys
from pathlib import Path

import yaml

from birdnet_config import find_birdnet_config_path, get_birdnet_config_info

# Dossiers typiques où chercher birdnet.db ou config BirdNET-Go
SEARCH_DIRS = [
    Path.home() / "birdnet-go-app" / "data",
    Path.home() / "BirdNET-Go",
    Path.home() / "birdnet-go-app",
    Path.cwd(),
    Path.cwd() / "data",
    Path("/opt") / "birdnet-go",
]

# Noms de fichiers DB possibles
DB_NAMES = ["birdnet.db", "birdnet_v2.db"]

# Fichier config BirdNET-Go (pour en extraire le chemin SQLite et clips)
BIRDNET_CONFIG_NAMES = ["config.yaml", "config.yml"]


def find_birdnet_config_dirs() -> list[Path]:
    """Retourne les dossiers contenant un config.yaml (config BirdNET-Go)."""
    found = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for name in BIRDNET_CONFIG_NAMES:
            cfg = d / name
            if cfg.is_file():
                found.append(d)
                break
            # config peut être dans un sous-dossier "config"
            cfg2 = d / "config" / name
            if cfg2.is_file():
                found.append(d)
                break
    return found


def find_database_files() -> list[Path]:
    """Retourne tous les birdnet.db trouvés dans les dossiers de recherche."""
    found = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for name in DB_NAMES:
            db = d / name
            if db.is_file():
                found.append(db.resolve())
    # Dédupliquer (même fichier via chemins différents)
    seen = set()
    unique = []
    for p in found:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def read_birdnet_config(config_dir: Path) -> dict:
    """Lit la config BirdNET-Go pour extraire database path et clips path."""
    data = {}
    for name in BIRDNET_CONFIG_NAMES:
        for base in [config_dir, config_dir / "config"]:
            cfg = base / name
            if cfg.is_file():
                try:
                    with open(cfg, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    return data
                except Exception:
                    pass
    return data


def get_sqlite_path_from_birdnet_config(config_dir: Path) -> Path | None:
    """Extrait le chemin de la base SQLite depuis la config BirdNET-Go."""
    data = read_birdnet_config(config_dir)
    path = (data.get("output") or {}).get("sqlite", {}).get("path")
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        # Config BirdNET-Go est souvent dans .../config/config.yaml, base dans .../data/
        p = config_dir / p
    if not p.is_file():
        # Essayer data/birdnet.db si config dit "birdnet.db"
        alt = config_dir / "data" / Path(path).name
        if alt.is_file():
            return alt
        return None
    return p


def get_clips_path_from_birdnet_config(config_dir: Path) -> Path | None:
    """Extrait le chemin des clips depuis la config BirdNET-Go."""
    data = read_birdnet_config(config_dir)
    export = (data.get("realtime") or {}).get("audio", {}).get("export", {})
    path = export.get("path")
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = config_dir / p
    if not p.is_dir():
        # Essayer data/clips si config dit "clips/"
        alt = config_dir / "data" / path.strip("/")
        if alt.is_dir():
            return alt
        return None
    return p


SYSTEMD_SERVICE_NAME = "birdnet-api2ha"
SYSTEMD_UNIT_TEMPLATE = """[Unit]
Description=BirdNET-Go API to Home Assistant (birdnet-api2ha)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={workdir}

# Python du venv (pas besoin d'activer le venv)
ExecStart={python_path} main.py{mqtt_flag}
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""


def generate_systemd_unit(project_dir: Path, user: str, with_mqtt: bool) -> str:
    """Génère le contenu du fichier unit systemd (utilise toujours venv/bin/python)."""
    # Sous Linux/Raspberry le venv expose venv/bin/python
    python_path = project_dir / "venv" / "bin" / "python"
    mqtt_flag = " --mqtt" if with_mqtt else ""
    return SYSTEMD_UNIT_TEMPLATE.format(
        user=user,
        group=user,
        workdir=str(project_dir.resolve()),
        python_path=str(python_path.resolve()),
        mqtt_flag=mqtt_flag,
    )


def install_systemd_service(service_path: Path) -> bool:
    """Copie le fichier service vers /etc/systemd/system/ et active le service. Nécessite sudo."""
    try:
        subprocess.run(
            ["sudo", "cp", str(service_path), f"/etc/systemd/system/{SYSTEMD_SERVICE_NAME}.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["sudo", "systemctl", "enable", SYSTEMD_SERVICE_NAME], check=True, capture_output=True)
        subprocess.run(["sudo", "systemctl", "start", SYSTEMD_SERVICE_NAME], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_interactive() -> dict:
    """Pose des questions et retourne la config à écrire."""
    print("=== birdnet-api2ha - Configuration ===\n")

    # 1) Recherche des bases
    db_files = find_database_files()
    config_dirs = find_birdnet_config_dirs()

    # Enrichir avec les chemins lus depuis les configs BirdNET-Go
    for cdir in config_dirs:
        db_from_config = get_sqlite_path_from_birdnet_config(cdir)
        if db_from_config and db_from_config not in db_files:
            db_files.append(db_from_config)

    if not db_files:
        print("Aucune base BirdNET-Go (birdnet.db) trouvée.")
        db_path = input("Chemin vers birdnet.db (obligatoire): ").strip()
        if not db_path:
            print("Erreur: chemin requis.")
            sys.exit(1)
        db_path = str(Path(db_path).expanduser().resolve())
        clips_path = input("Chemin des clips (optionnel, Enter pour vide): ").strip()
        clips_path = str(Path(clips_path).expanduser().resolve()) if clips_path else ""
    else:
        print("Base(s) BirdNET-Go trouvée(s):")
        for i, p in enumerate(db_files, 1):
            print(f"  {i}. {p}")
        if len(db_files) == 1:
            db_path = str(db_files[0])
            print(f"Utilisation: {db_path}")
        else:
            choice = input(f"Choisir (1-{len(db_files)}) [1]: ").strip() or "1"
            try:
                idx = int(choice)
                db_path = str(db_files[idx - 1])
            except (ValueError, IndexError):
                db_path = str(db_files[0])

        # Clips: chercher depuis une config BirdNET-Go
        clips_path = ""
        for cdir in config_dirs:
            clips_path_candidate = get_clips_path_from_birdnet_config(cdir)
            if clips_path_candidate:
                clips_path = str(clips_path_candidate)
                print(f"Chemin clips (depuis config BirdNET-Go): {clips_path}")
                break
        if not clips_path:
            default_clips = str(Path(db_path).parent / "clips")
            clips_path = input(f"Chemin des clips (optionnel) [{default_clips}]: ").strip()
            clips_path = clips_path or default_clips
            if not Path(clips_path).is_dir():
                clips_path = ""

    # 2) Port
    port_str = input("Port HTTP (défaut 8081) [8081]: ").strip() or "8081"
    try:
        port = int(port_str)
    except ValueError:
        port = 8081

    # birdnet_config_path pour /api/birdnet-config (type SQLite/MySQL, nom DB)
    birdnet_config_path = ""
    cfg_path = find_birdnet_config_path(db_path)
    if cfg_path:
        birdnet_config_path = str(cfg_path)
        info = get_birdnet_config_info(db_path, birdnet_config_path)
        if info:
            print(f"Config BirdNET-Go: type={info['database_type']}, config={cfg_path}")

    # 3) MQTT
    mqtt_enabled = input("Activer le pont MQTT ? (o/N): ").strip().lower() in ("o", "y", "yes")
    mqtt_host = "localhost"
    mqtt_port = 1883
    mqtt_username = ""
    mqtt_password = ""
    mqtt_topic = "birdnet_api2ha/detections"
    if mqtt_enabled:
        mqtt_host = input("Broker MQTT (défaut localhost) [localhost]: ").strip() or "localhost"
        mqtt_port_str = input("Port MQTT (défaut 1883) [1883]: ").strip() or "1883"
        try:
            mqtt_port = int(mqtt_port_str)
        except ValueError:
            mqtt_port = 1883
        mqtt_username = input("Utilisateur MQTT (vide si aucun) []: ").strip()
        mqtt_password = getpass.getpass("Mot de passe MQTT (vide si aucun): ")
        mqtt_topic = input("Topic (défaut birdnet_api2ha/detections) [birdnet_api2ha/detections]: ").strip() or mqtt_topic

    # 4) Démarrage automatique (systemd)
    config = {
        "database_path": db_path,
        "clips_base_path": clips_path if clips_path else "",
        "birdnet_config_path": birdnet_config_path,
        "http_host": "0.0.0.0",
        "http_port": port,
        "mqtt": {
            "enabled": mqtt_enabled,
            "host": mqtt_host,
            "port": mqtt_port,
            "username": mqtt_username,
            "password": mqtt_password,
            "topic": mqtt_topic,
            "poll_interval_seconds": 10,
        },
    }

    setup_systemd = input("Configurer le démarrage automatique au boot (systemd) ? (o/N): ").strip().lower() in ("o", "y", "yes")
    if setup_systemd:
        project_dir = Path(__file__).resolve().parent
        venv_python = project_dir / "venv" / "bin" / "python"
        if not venv_python.is_file():
            print("  Attention: venv non trouvé (venv/bin/python). Créez-le avec: python3 -m venv venv && source venv/bin/activate")
        user = getpass.getuser()
        mqtt_at_boot = input("  Inclure le pont MQTT au démarrage ? (o/N): ").strip().lower() in ("o", "y", "yes")
        content = generate_systemd_unit(project_dir, user, mqtt_at_boot)
        service_file = project_dir / f"{SYSTEMD_SERVICE_NAME}.service"
        service_file.write_text(content, encoding="utf-8")
        print(f"  Fichier créé: {service_file}")
        print("  Pour installer et activer le service:")
        print(f"    sudo cp {service_file} /etc/systemd/system/")
        print("    sudo systemctl daemon-reload")
        print(f"    sudo systemctl enable {SYSTEMD_SERVICE_NAME}")
        print(f"    sudo systemctl start {SYSTEMD_SERVICE_NAME}")
        install_now = input("  Installer et démarrer le service maintenant ? (nécessite sudo) (o/N): ").strip().lower() in ("o", "y", "yes")
        if install_now:
            if install_systemd_service(service_file):
                print("  Service installé et démarré.")
            else:
                print("  Échec (sudo ?). Exécutez les commandes ci-dessus à la main.")

    return config


def run_non_interactive() -> dict:
    """Configuration automatique sans questions (utilise la première base trouvée)."""
    db_files = find_database_files()
    config_dirs = find_birdnet_config_dirs()
    for cdir in config_dirs:
        db_from_config = get_sqlite_path_from_birdnet_config(cdir)
        if db_from_config and db_from_config not in db_files:
            db_files.append(db_from_config)

    if not db_files:
        raise FileNotFoundError(
            "Aucune base birdnet.db trouvée. Lancez sans --non-interactive pour saisir le chemin."
        )
    db_path = str(db_files[0])
    clips_path = ""
    for cdir in config_dirs:
        clips_path_candidate = get_clips_path_from_birdnet_config(cdir)
        if clips_path_candidate:
            clips_path = str(clips_path_candidate)
            break
    if not clips_path and db_files:
        candidate = Path(db_path).parent / "clips"
        if candidate.is_dir():
            clips_path = str(candidate)

    birdnet_config_path = ""
    cfg_path = find_birdnet_config_path(db_path)
    if cfg_path:
        birdnet_config_path = str(cfg_path)

    return {
        "database_path": db_path,
        "clips_base_path": clips_path,
        "birdnet_config_path": birdnet_config_path,
        "http_host": "0.0.0.0",
        "http_port": 8081,
        "mqtt": {
            "enabled": False,
            "host": "localhost",
            "port": 1883,
            "username": "",
            "password": "",
            "topic": "birdnet_api2ha/detections",
            "poll_interval_seconds": 10,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Configurer birdnet-api2ha (recherche DB et config)")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Tout configurer automatiquement (première base trouvée, pas de questions)",
    )
    parser.add_argument(
        "-o", "--output",
        default="config.yaml",
        help="Fichier de sortie (défaut: config.yaml)",
    )
    args = parser.parse_args()

    try:
        if args.non_interactive:
            config = run_non_interactive()
            print(f"Configuration automatique: base={config['database_path']}, port={config['http_port']}")
        else:
            config = run_interactive()
    except FileNotFoundError as e:
        print(f"Erreur: {e}")
        sys.exit(1)

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"\nConfig enregistrée: {out_path.resolve()}")
    print("Lancez: python main.py   ou   python main.py --mqtt")


if __name__ == "__main__":
    main()
