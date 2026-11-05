#!/bin/sh
# Signal de surveillance : prévient le service externe que le bot tourne.
# Appelé toutes les 5 minutes par un timer systemd. Si le bot est arrêté,
# aucun signal n'est envoyé et le service t'alerte après le délai de grâce.
# Variable (fichier d'environnement du service, hors dépôt) : PING_BOT
set -eu

[ -n "${PING_BOT:-}" ] || { echo "PING_BOT manquant" >&2; exit 1; }
systemctl is-active --quiet agentski || exit 0
curl -fsS -m 10 --retry 3 -o /dev/null "$PING_BOT"
