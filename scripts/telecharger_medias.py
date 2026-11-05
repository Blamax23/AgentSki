"""Télécharge les médias reçus par le bot dans medias/<jour>/<prénom>/ (photos, vidéos, vocaux de 20 Mo ou moins).
Les plus gros sont listés : le bot les renvoie avec /medias, et Telegram les télécharge en taille réelle.
À lancer là où se trouve la base (ton ordinateur, ou le VPS) :  .venv/bin/python scripts/telecharger_medias.py [dossier]"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.getLogger("httpx").setLevel(logging.WARNING)  # les adresses de l'API contiennent le jeton : jamais dans la sortie

from agentski import config, telechargement  # noqa: E402
from agentski.db import SessionLocal  # noqa: E402

if not config.TELEGRAM_BOT_TOKEN:
    sys.exit("TELEGRAM_BOT_TOKEN absent du fichier .env")
dossier = Path(sys.argv[1] if len(sys.argv) > 1 else config.ROOT / "medias")
with SessionLocal() as session:
    bilan = telechargement.telecharger_tout(session, dossier, telechargement.ClientTelegram(config.TELEGRAM_BOT_TOKEN))
print(f"{bilan.telecharges} téléchargé(s), {bilan.deja_la} déjà présent(s) dans {dossier}")
if bilan.gros:
    print(f"\n{len(bilan.gros)} trop lourd(s) pour l'API des bots (plus de 20 Mo) : écris /medias au bot, il te les renvoie, "
          "puis télécharge-les depuis Telegram :")
    print("\n".join(f"  - {g}" for g in bilan.gros))
if bilan.echecs:
    print(f"\n{len(bilan.echecs)} échec(s) :")
    print("\n".join(f"  - {e}" for e in bilan.echecs))
    sys.exit(1)
