# Historique des versions

## 1.1.4 — 2026-05-09

- **`timezone`** (IANA, ex. `America/Toronto`) dans `config.yaml` : pour le schéma v2, `/api/hourly` agrège les heures avec **`zoneinfo`** (bornes minuit local → minuit+1), indépendamment du fuseau du processus / Docker UTC. Corrige le décalage des colonnes 0–23 vs l’heure réelle.
- **`sunrise_hour` / `sunset_hour`** : calcul avec le même fuseau lorsque `timezone` est défini ; champ **`timezone`** dans la réponse JSON.

## 1.1.2 — 2026-05-09

- **CORS** : configuration `cors.allowed_origins` dans `config.yaml` pour autoriser les requêtes des custom cards Lovelace depuis le navigateur (même origine Home Assistant → API sur un autre hôte).
- Dépendance **`flask-cors`** ajoutée dans `requirements.txt`.
- Mise à jour de **`config.yaml.example`** et du **README** (section carte Lovelace / CORS).

Les versions précédentes ne sont pas listées ici ; consulter l’historique Git pour le détail.
