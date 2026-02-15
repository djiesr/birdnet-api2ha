# birdnet-api2ha

**Pont entre BirdNET-Go et Home Assistant** sans modifier le code de BirdNET-Go.  
Ce service lit la base SQLite de BirdNET-Go en lecture seule et expose une API REST + option MQTT pour Home Assistant.

## Fonctionnalités

- **API REST** (même contrat que l’API “Home Assistant” du fork)  
  - `GET /api/detections` — liste des détections (filtres: `date_start`, `date_end`, `common_name`, `limit`)  
  - `GET /api/stats` — comptage par espèce sur une période  
- **Pont MQTT** (optionnel) : surveille la base et publie chaque **nouvelle** détection sur un topic dédié (ex. `birdnet_api2ha/detections`). Home Assistant peut s’abonner à ce topic sans toucher à la config MQTT de BirdNET-Go.

## Prérequis

- Python 3.10+
- Base BirdNET-Go : **schéma v2** uniquement pour l’instant (tables `detections` + `labels`).  
  Si ta base est en ancien schéma (“notes”), ouvre une issue et on pourra ajouter le support.

## Installation

```bash
git clone https://github.com/djiesr/birdnet-api2ha.git
cd birdnet-api2ha
cp config.yaml.example config.yaml
# Éditer config.yaml : database_path, port, MQTT si besoin
pip install -r requirements.txt
```

## Configuration

Dans `config.yaml` (voir `config.yaml.example`) :

- **database_path** : chemin vers `birdnet.db` (ex. `/home/djiesr/birdnet-go-app/data/birdnet.db`).
- **http_port** : port du serveur HTTP (défaut 8081 pour ne pas conflit avec BirdNET-Go sur 8080).
- **mqtt** : si `enabled: true`, le pont publie les nouvelles détections sur **topic** (défaut `birdnet_api2ha/detections`). Même broker que BirdNET-Go ou autre, au choix.

Variables d’environnement optionnelles :

- `BIRDNET_API2HA_CONFIG` — chemin vers le fichier de config.
- `BIRDNET_API2HA_DB` — chemin vers la base (override).
- `BIRDNET_API2HA_PORT` — port HTTP (override).

## Lancement

```bash
# API seule
python main.py

# API + pont MQTT (nouvelles détections publiées sur MQTT)
python main.py --mqtt
```

En production (ex. sur un Raspberry) : utiliser un service systemd ou un venv + `pip install -r requirements.txt`.

## Home Assistant

- **REST** : capteurs REST sur `http://IP_DU_PI:8081/api/stats` et `http://IP_DU_PI:8081/api/detections?limit=10`. Exemples de capteurs et automatisations dans la doc d’intégration HA (voir dépôt BirdNET-Go ou ce README).
- **MQTT** : s’abonner au topic `birdnet_api2ha/detections` ; chaque message = une détection (JSON avec `common_name`, `scientific_name`, `confidence`, `timestamp`, `id`). Tu peux filtrer migrateurs / espèces côté HA.

## Backup et base de test

Tu peux pointer `database_path` vers une **copie** de ta base (ex. ton backup `birdnet-backup-20260215-0937/birdnet.db`) pour tester sans toucher à l’installation BirdNET-Go en production.

## Créer le dépôt GitHub

1. Sur GitHub : **New repository** → nom **birdnet-api2ha** (pas de README initial si tu clones ci‑dessous).
2. En local (dans le dossier du projet) :
   ```bash
   cd birdnet-api2ha
   git init
   git add .
   git commit -m "Initial: API REST + pont MQTT pour Home Assistant"
   git remote add origin https://github.com/djiesr/birdnet-api2ha.git
   git branch -M main
   git push -u origin main
   ```

## Licence

MIT.
