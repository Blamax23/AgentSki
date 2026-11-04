#!/bin/sh
# Lance le bot en local : applique les migrations puis démarre
cd "$(dirname "$0")"
.venv/bin/alembic upgrade head && exec .venv/bin/python -m agentski
