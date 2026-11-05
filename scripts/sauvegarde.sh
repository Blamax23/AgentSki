#!/bin/sh
# Sauvegarde nocturne : copie cohérente de la base (le bot peut tourner), compressée,
# envoyée chiffrée hors du VPS par rclone, puis purge des copies de plus de 30 jours.
# Variables (fichier d'environnement du service, hors dépôt) :
#   PING_SAUVEGARDE  URL healthchecks de la sauvegarde (facultatif)
#   RCLONE_REMOTE    remote rclone chiffré, par défaut "agentski-crypt:"
set -eu
cd "$(dirname "$0")/.." || exit 1

REMOTE="${RCLONE_REMOTE:-agentski-crypt:}"
PING="${PING_SAUVEGARDE:-}"
TMP=$(mktemp -d)

ping() { [ -n "$PING" ] && curl -fsS -m 10 --retry 3 -o /dev/null "$PING$1" || true; }
fin() { code=$?; rm -rf "$TMP"; [ "$code" -eq 0 ] || ping /fail; exit "$code"; }
trap fin EXIT

ping /start
.venv/bin/python - "$TMP/agentski.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect("data/agentski.db")
copie = sqlite3.connect(sys.argv[1])
source.backup(copie)
copie.close()
source.close()
PY
gzip "$TMP/agentski.db"
rclone copyto "$TMP/agentski.db.gz" "${REMOTE}agentski-$(date +%Y%m%d-%H%M).db.gz"
rclone delete --min-age 30d "$REMOTE"
ping ""
