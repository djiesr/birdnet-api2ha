# birdnet-api2ha

**Pont entre BirdNET-Go et Home Assistant** sans modifier le code de BirdNET-Go.  
Ce service lit la base SQLite de BirdNET-Go en lecture seule et expose une API REST + option MQTT pour Home Assistant.

> **Partie de l'écosystème birdnet-api2ha.**
> Le dépôt principal (intégration HA) est [birdnet-api2ha-custom_components](https://github.com/djiesr/birdnet-api2ha-custom_components).

---

## Écosystème

| Dépôt | Rôle |
|-------|------|
| [birdnet-api2ha-custom_components](https://github.com/djiesr/birdnet-api2ha-custom_components) | **Intégration HA** — dépôt principal, capteurs et binary sensors |
| **birdnet-api2ha** *(ce dépôt)* | **API REST** — lit la base BirdNET-Go, sert les données |
| [birdnet-api2ha-custom_card](https://github.com/djiesr/birdnet-api2ha-custom_card) | **Carte Lovelace** — heatmap d'activité, addon optionnel |

---

## Fonctionnalités

- **API REST** (même contrat que l’API “Home Assistant” du fork)  
  - `GET /api/detections` — liste des détections (filtres: `date_start`, `date_end`, `common_name`, `limit`)  
  - `GET /api/stats` — comptage par espèce sur une période  
- **Pont MQTT** (optionnel) : surveille la base et publie chaque **nouvelle** détection sur un topic dédié (ex. `birdnet_api2ha/detections`). Home Assistant peut s’abonner à ce topic sans toucher à la config MQTT de BirdNET-Go.

## Prérequis

- **Python 3.10+**
- Base BirdNET-Go : **schéma v2** (tables `detections` + `labels`) ou **schéma legacy** (table `notes`).
- Sous **Debian / Ubuntu / Raspberry Pi OS** : le système impose un **environnement virtuel (venv)** pour installer des paquets Python (PEP 668). On ne peut pas faire `pip install` global ; il faut donc créer un venv (voir ci‑dessous).

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/djiesr/birdnet-api2ha.git
cd birdnet-api2ha
```

### 2. Créer et activer un environnement virtuel (venv)

Sous Linux / Raspberry Pi OS (obligatoire pour éviter les conflits et respecter PEP 668) :

```bash
# Créer le venv dans le dossier du projet (répertoire nommé venv)
python3 -m venv venv

# Activer le venv (à faire à chaque nouvelle session terminal)
source venv/bin/activate
```

Sous Windows (PowerShell ou CMD) :

```powershell
python -m venv venv
venv\Scripts\activate
```

Si la commande `python3 -m venv` échoue (paquet manquant), installe le module venv :

```bash
sudo apt update
sudo apt install python3-venv
# ou, selon la distro :
sudo apt install python3-full
```

Une fois le venv activé, l’invite affiche `(venv)` au début de la ligne.

### 3. Installer les dépendances

**Toujours avec le venv activé** (`source venv/bin/activate` sous Linux) :

```bash
pip install -r requirements.txt
```

### 4. Configurer l’API

```bash
# Mode guidé (questions interactives)
python configure.py

# Ou mode automatique (valeurs par défaut, première base BirdNET-Go trouvée)
python configure.py --non-interactive
```

Cela crée ou met à jour **config.yaml** (chemin base SQLite, port HTTP, option MQTT, etc.). Tu peux aussi copier `config.yaml.example` en `config.yaml` et modifier à la main.

En mode guidé, **configure.py** propose aussi de **configurer le démarrage automatique au boot (systemd)** : il génère le fichier `birdnet-api2ha.service` avec les bons chemins (venv, utilisateur) et peut, si tu le souhaites, l’installer et l’activer tout de suite (nécessite `sudo`).

### 5. Lancer le service (test)

```bash
# API seule
python main.py

# API + pont MQTT (publication des nouvelles détections sur MQTT)
python main.py --mqtt
```

L’API répond par exemple sur `http://IP_DU_PI:8081` (port par défaut 8081). Pour un lancement permanent au démarrage du Pi, utilise un service systemd (voir section suivante).

## Configuration automatique ou guidée

Le script **configure.py** cherche tout seul la base BirdNET-Go et (si trouvée) la config BirdNET-Go pour en déduire le chemin des clips, puis pose quelques questions (port, MQTT) et écrit **config.yaml**.

```bash
# Avec le venv activé (source venv/bin/activate)
# Mode guidé (questions interactives)
python configure.py

# Mode automatique (première base trouvée, valeurs par défaut, pas de questions)
python configure.py --non-interactive
```

Recherche effectuée dans :

- `~/birdnet-go-app/data/`, `~/BirdNET-Go/`, répertoire courant, etc.
- Si un **config.yaml** BirdNET-Go est trouvé, les champs `output.sqlite.path` et `realtime.audio.export.path` sont lus pour remplir `database_path` et `clips_base_path`.

Ensuite : `python main.py` ou `python main.py --mqtt`.

## Configuration manuelle

Dans `config.yaml` (voir `config.yaml.example`) :

- **database_path** : chemin vers `birdnet.db` (ex. `/home/djiesr/birdnet-go-app/data/birdnet.db`).
- **http_port** : port du serveur HTTP (défaut 8081 pour ne pas conflit avec BirdNET-Go sur 8080).
- **mqtt** : si `enabled: true`, le pont publie les nouvelles détections sur **topic** (défaut `birdnet_api2ha/detections`). Même broker que BirdNET-Go ou autre, au choix.

Variables d’environnement optionnelles :

- `BIRDNET_API2HA_CONFIG` — chemin vers le fichier de config.
- `BIRDNET_API2HA_DB` — chemin vers la base (override).
- `BIRDNET_API2HA_PORT` — port HTTP (override).

## Lancement manuel

Avec le venv activé (`source venv/bin/activate`) :

```bash
# API seule
python main.py

# API + pont MQTT (nouvelles détections publiées sur MQTT)
python main.py --mqtt
```

## Démarrage automatique au boot (systemd)

Sur un Raspberry Pi ou un serveur Linux, tu peux faire démarrer **birdnet-api2ha** au boot avec un service **systemd**. Le service utilisera le **venv** du projet (pas besoin d’activer le venv à la main).

### 1. Créer le fichier service

Édite (ou crée) le fichier unit systemd (remplace `ton_utilisateur` par ton nom d’utilisateur Linux et `birdnet-api2ha` par le chemin réel du projet si différent) :

```bash
sudo nano /etc/systemd/system/birdnet-api2ha.service
```

Contenu proposé (à adapter) :

```ini
[Unit]
Description=BirdNET-Go API to Home Assistant (birdnet-api2ha)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ton_utilisateur
Group=ton_utilisateur
WorkingDirectory=/home/ton_utilisateur/birdnet-api2ha

# Utiliser le Python du venv (pas besoin d'activer le venv)
ExecStart=/home/ton_utilisateur/birdnet-api2ha/venv/bin/python main.py
# Pour activer aussi le pont MQTT, utilise plutôt :
# ExecStart=/home/ton_utilisateur/birdnet-api2ha/venv/bin/python main.py --mqtt

Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

- **User/Group** : l’utilisateur qui lance le service (doit avoir accès à la base BirdNET-Go et au dossier du projet).
- **WorkingDirectory** : le dossier où se trouvent `main.py`, `config.yaml`, etc.
- **ExecStart** : le binaire Python **dans le venv** (`.../venv/bin/python`) + `main.py` ou `main.py --mqtt`.

### 2. Activer et démarrer le service

```bash
# Recharger systemd pour prendre en compte le nouveau fichier
sudo systemctl daemon-reload

# Activer le service au démarrage
sudo systemctl enable birdnet-api2ha

# Démarrer tout de suite (sans redémarrer la machine)
sudo systemctl start birdnet-api2ha

# Vérifier le statut
sudo systemctl status birdnet-api2ha
```

### 3. Commandes utiles

```bash
# Voir les logs en direct
sudo journalctl -u birdnet-api2ha -f

# Arrêter le service
sudo systemctl stop birdnet-api2ha

# Désactiver le démarrage au boot
sudo systemctl disable birdnet-api2ha
```

Après une mise à jour du code ou du venv (`pip install -r requirements.txt`), un simple redémarrage du service suffit :

```bash
sudo systemctl restart birdnet-api2ha
```

## Mise à jour

### Avec systemd (recommandé)

```bash
# 1. Se placer dans le dossier du projet
cd ~/birdnet-api2ha

# 2. Récupérer la dernière version
git pull origin master

# 3. Activer le venv et mettre à jour les dépendances
source venv/bin/activate
pip install -r requirements.txt

# 4. Redémarrer le service
sudo systemctl restart birdnet-api2ha

# 5. Vérifier que tout tourne
sudo systemctl status birdnet-api2ha
```

### Sans systemd (lancement manuel)

```bash
# Arrêter le processus en cours (Ctrl+C dans le terminal où il tourne, ou)
pkill -f "python main.py"

# Mettre à jour
cd ~/birdnet-api2ha
git pull origin master
source venv/bin/activate
pip install -r requirements.txt

# Relancer
python main.py
# ou avec MQTT :
python main.py --mqtt
```

### NAS / Docker (archive `.tar.gz`, sans `git`)

Pour un conteneur qui monte le code depuis un dossier partagé (ex. UGREEN), remplacez le chemin et la **version** (`v1.1.4`) par celle de la [release souhaitée](https://github.com/djiesr/birdnet-api2ha/releases).

```bash
sudo docker stop birdnet-api2ha
sudo cp /volume1/Docker/birdnet-api2ha/config.yaml /tmp/config-birdnet-api2ha.yaml.bak
sudo rm -rf /volume1/Docker/birdnet-api2ha
cd /tmp
sudo curl -L -o birdnet-api2ha.tar.gz "https://github.com/djiesr/birdnet-api2ha/archive/refs/tags/v1.1.4.tar.gz"
sudo tar xzf birdnet-api2ha.tar.gz
sudo mv birdnet-api2ha-1.1.4 /volume1/Docker/birdnet-api2ha
sudo cp /tmp/config-birdnet-api2ha.yaml.bak /volume1/Docker/birdnet-api2ha/config.yaml
sudo docker start birdnet-api2ha
sudo docker logs --tail 40 birdnet-api2ha
curl -s http://127.0.0.1:8081/health
```

- Le dossier extrait s’appelle **`birdnet-api2ha-1.1.4`** (même numéro que le tag, avec un tiret).
- Après copie de `config.yaml`, vérifiez qu’il contient toujours **`cors:`**, **`timezone:`** (IANA, ex. `America/Toronto`) et les chemins `database_path` / volumes.
- Si le port hôte n’est pas **8081**, adaptez l’URL du `curl`.

### Vérifier la version installée

```bash
cd ~/birdnet-api2ha && git log --oneline -1
```

### Voir les logs après mise à jour

```bash
sudo journalctl -u birdnet-api2ha -f
```

> **Note** : après chaque mise à jour, l’API est accessible sur `http://IP_DU_PI:8081/`. Le endpoint `/health` confirme que le service est opérationnel.

## Home Assistant

L’intégration recommandée est **[birdnet-api2ha-custom_components](https://github.com/djiesr/birdnet-api2ha-custom_components)** — elle crée automatiquement tous les capteurs, binary sensors et gère la résilience.

- **REST** : les endpoints `/api/stats`, `/api/detections`, `/api/hourly`, `/api/aggregate`, `/api/system` sont documentés dans l’intégration HA.
- **MQTT** : s’abonner au topic `birdnet_api2ha/detections` ; chaque message = une détection (JSON avec `common_name`, `scientific_name`, `confidence`, `timestamp`, `id`).
- **Carte Lovelace** : voir [birdnet-api2ha-custom_card](https://github.com/djiesr/birdnet-api2ha-custom_card) pour le tableau d’activité heatmap.

### Carte Lovelace : CORS (important)

La carte Lovelace fait des requêtes **depuis le navigateur** (origine Home Assistant, ex. `http://homeassistant.local:8123`) vers l’API (ex. `http://IP_DU_PI:8081`). Il faut donc autoriser cette origine via CORS dans `config.yaml` :

```yaml
cors:
  allowed_origins:
    - "http://homeassistant.local:8123"
    # - "http://192.168.10.50:8123"  # si vous accédez à HA via IP
```

### Fuseau horaire (Docker / serveur)

L’API regroupe les détections par heure et par jour avec SQLite (`datetime(..., 'localtime')`). L’« heure locale » utilisée est celle du **processus Python** (système / conteneur).

- Sous **Docker**, sans `TZ`, l’image est souvent en **UTC** : les colonnes 0–23 h du mode « Heure » ne correspondront pas à votre fuseau.
- **Recommandation :** définir la même variable d’environnement **`TZ`** que pour BirdNET-Go, par exemple :

```yaml
environment:
  - TZ=America/Toronto
```

(Exemple pour un service `birdnet-api2ha` dans Docker Compose ; adaptez le fuseau IANA à votre région.)

Puis redémarrez le conteneur. Sur un Raspberry avec systemd, le fuseau suit en général celui du système (`timedatectl`).

L’endpoint **`GET /api/hourly`** renvoie aussi **`sunrise_hour`** et **`sunset_hour`** (entiers 0–23), calculés avec le **même fuseau** que les colonnes horaires, pour que la barre *Daylight* de la carte Lovelace s’aligne sur la grille.

### Fuseau explicite `timezone` (recommandé en Docker UTC)

Même avec **`TZ`** sur le conteneur, SQLite `localtime` peut rester décalé selon l’image. Pour que **`hourly_counts[0..23]`** corresponde au fuseau du jardin / de BirdNET-Go, définissez dans **`config.yaml`** :

```yaml
timezone: "America/Toronto"
```

(valeur [IANA](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), ex. `Europe/Paris`.)  
Avec **`timezone`** renseigné, l’API agrège les timestamps Unix avec **`zoneinfo`** (schéma v2) et renvoie aussi **`timezone`** dans le JSON. Sans cette clé, l’ancien mode SQLite `localtime` est conservé.

## Backup et base de test

Tu peux pointer `database_path` vers une **copie** de ta base (ex. ton backup `birdnet-backup-20260215-0937/birdnet.db`) pour tester sans toucher à l’installation BirdNET-Go en production.

## Créer / mettre à jour le dépôt GitHub

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
