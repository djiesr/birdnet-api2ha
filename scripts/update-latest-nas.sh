#!/usr/bin/env bash
# Met à jour le dossier code birdnet-api2ha sur le NAS avec la dernière release GitHub.
#
# Une ligne (télécharge le script puis l’exécute en root) :
#   curl -fsSL "https://raw.githubusercontent.com/djiesr/birdnet-api2ha/master/scripts/update-latest-nas.sh" | sudo bash
#
# Ou après git clone, depuis le dépôt :
#   sudo bash scripts/update-latest-nas.sh
#
# Variables d’environnement (optionnelles) :
#   BIRDNET_API2HA_DIR   chemin du code sur le disque (défaut: /volume1/Docker/birdnet-api2ha)
#   BIRDNET_API2HA_NAME  nom du conteneur Docker (défaut: birdnet-api2ha)
#   GITHUB_REPO          owner/repo (défaut: djiesr/birdnet-api2ha)

set -euo pipefail

BIRDNET_API2HA_DIR="${BIRDNET_API2HA_DIR:-/volume1/Docker/birdnet-api2ha}"
BIRDNET_API2HA_NAME="${BIRDNET_API2HA_NAME:-birdnet-api2ha}"
GITHUB_REPO="${GITHUB_REPO:-djiesr/birdnet-api2ha}"
TMP_ARCHIVE="/tmp/birdnet-api2ha-release.tar.gz"
TMP_CONFIG="/tmp/config-birdnet-api2ha.yaml.bak"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Erreur: binaire requis introuvable: $1" >&2; exit 1; }
}

need_cmd curl
need_cmd tar
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Erreur: python3 (ou python) requis pour lire l'API GitHub." >&2
  exit 1
fi

echo "→ Dernière release GitHub (${GITHUB_REPO})…"
TAG="$(curl -sSL "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"

DOWNLOAD_URL="https://github.com/${GITHUB_REPO}/archive/refs/tags/${TAG}.tar.gz"

echo "→ Tag: ${TAG}"
echo "→ Arrêt du conteneur ${BIRDNET_API2HA_NAME}…"
docker stop "${BIRDNET_API2HA_NAME}" 2>/dev/null || true

echo "→ Sauvegarde config.yaml…"
if [[ -f "${BIRDNET_API2HA_DIR}/config.yaml" ]]; then
  cp "${BIRDNET_API2HA_DIR}/config.yaml" "${TMP_CONFIG}"
else
  echo "  (aucun config.yaml à ${BIRDNET_API2HA_DIR}, on continue)"
fi

echo "→ Remplacement du dossier ${BIRDNET_API2HA_DIR}…"
rm -rf "${BIRDNET_API2HA_DIR}"
mkdir -p "$(dirname "${BIRDNET_API2HA_DIR}")"

curl -fsSL -o "${TMP_ARCHIVE}" "${DOWNLOAD_URL}"

ROOT="$(tar tzf "${TMP_ARCHIVE}" | head -1 | cut -d/ -f1)"
if [[ -z "${ROOT}" ]]; then
  echo "Erreur: impossible de lire l'archive." >&2
  exit 1
fi

rm -rf "/tmp/${ROOT}"
tar xzf "${TMP_ARCHIVE}" -C /tmp
rm -f "${TMP_ARCHIVE}"

mv "/tmp/${ROOT}" "${BIRDNET_API2HA_DIR}"

if [[ -f "${TMP_CONFIG}" ]]; then
  cp "${TMP_CONFIG}" "${BIRDNET_API2HA_DIR}/config.yaml"
  echo "→ config.yaml restauré."
fi

echo "→ Démarrage du conteneur ${BIRDNET_API2HA_NAME}…"
if docker start "${BIRDNET_API2HA_NAME}" 2>/dev/null; then
  docker logs --tail 30 "${BIRDNET_API2HA_NAME}" 2>/dev/null || true
else
  echo "  (docker start a échoué — démarrez le stack depuis l’UI ou: docker compose up -d)"
fi

echo "→ Terminé. Dossier: ${BIRDNET_API2HA_DIR} (release ${TAG})"
