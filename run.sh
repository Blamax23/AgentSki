#!/bin/sh
# Lance le bot : prépare l'environnement au premier lancement, applique les migrations, puis démarre.
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; } || exit 1
.venv/bin/alembic upgrade head && exec .venv/bin/python -m agentski
