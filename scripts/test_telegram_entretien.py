"""Teste la couche Telegram de l'entretien (réception d'un vocal, /journal, supprime) avec de faux messages.
La transcription et l'extraction sont simulées : aucun appel réseau, mais ffmpeg tourne sur un vrai vocal.
Lancer : .venv/bin/python scripts/test_telegram_entretien.py"""
import asyncio
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "tg.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from sqlalchemy import select  # noqa: E402

from agentski import bot, entretien  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, ChampJournal, Journee, Personne, Presence, Transcription, Weekend  # noqa: E402

Base.metadata.create_all(engine)
# Un petit son généré par ffmpeg : le test marche sur une machine neuve, sans les vrais vocaux (qui ne sont pas dans git)
import subprocess  # noqa: E402
VOCAL = Path(tempfile.mkdtemp()) / "essai.ogg"
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(VOCAL)], check=True)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    w = Weekend(date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 12), statut="confirme")
    s.add(w)
    s.flush()
    j = Journee(weekend_id=w.id, jour=date(2026, 12, 12))
    s.add(j)
    s.flush()
    s.add(Presence(journee_id=j.id, personne_id=maxime.id))
    s.commit()
    JID, MID = j.id, maxime.id

# ffmpeg réel, réseau simulé
vus = {}
def fausse_transcription(morceaux):
    vus["morceaux"] = [m.name for m in morceaux]
    vus["existent"] = all(m.exists() for m in morceaux)
    return "Samedi à Flaine, grand soleil.", 0.001
def fausse_extraction(texte, jour, pistes, periode="bilan"):
    return {"stations": [{"valeur": "Flaine", "source": "à Flaine"}], "pistes": [], "meteo": {"valeur": "Grand soleil", "source": "grand soleil"},
            "neige": None, "ton": None, "moments": [], "chutes": []}
bot.ia.transcrire, bot.ia.extraire = fausse_transcription, fausse_extraction


async def telecharger(chemin):
    shutil.copy(VOCAL, chemin)


def vocal_update(taille=50_000):
    media = SimpleNamespace(file_size=taille, duration=15, get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=telecharger)))
    message = SimpleNamespace(voice=media, audio=None, reply_text=AsyncMock(), text=None, reply_to_message=None)
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=99)), message


def contexte():
    return SimpleNamespace(args=[], bot=SimpleNamespace(send_message=AsyncMock()))


async def main():
    up, msg = vocal_update()
    await bot.vocal(up, contexte())
    verifie("pas d'entretien en cours" in msg.reply_text.call_args[0][0], "vocal hors entretien : réponse claire, rien n'est traité")

    with SessionLocal() as s:
        entretien.demarrer(s, JID, MID)
    up, msg = vocal_update(taille=25 * 1024 * 1024)
    await bot.vocal(up, contexte())
    verifie("Trop lourd" in msg.reply_text.call_args[0][0], "vocal de plus de 20 Mo refusé")

    up, msg = vocal_update()
    ctx = contexte()
    await bot.vocal(up, ctx)
    envoyes = [c.args[1] for c in ctx.bot.send_message.call_args_list]
    verifie(vus["morceaux"] == ["morceau_000.mp3"] and vus["existent"], "le vocal est converti en MP3 avant la transcription")
    verifie(any("J'ai compris : « Samedi à Flaine, grand soleil. »" in t for t in envoyes), "le texte compris est renvoyé pour vérification")
    verifie(any("À midi" in t for t in envoyes), "puis la question suivante du fil est posée")
    with SessionLocal() as s:
        verifie(s.scalar(select(Transcription)).cout == 0.001, "coût conservé avec la transcription")

    up, msg = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1)), None
    ctx = contexte()
    ctx.args = ["2026-12-12"]
    await bot.commande_journal(up, ctx)
    verifie("Week-end du 12" in up.message.reply_text.call_args[0][0] or "Samedi 12 décembre" in up.message.reply_text.call_args[0][0], "/journal répond")

    with SessionLocal() as s:
        numero = s.scalar(select(ChampJournal.id).where(ChampJournal.cle == "meteo"))
    for texte, reponse in ((f"supprime #{numero}", None), ("supprime ça", "Quel élément")):
        message = SimpleNamespace(text=texte, reply_text=AsyncMock(), reply_to_message=None)
        await bot.supprime(SimpleNamespace(message=message), contexte())
        verifie(reponse in message.reply_text.call_args[0][0] if reponse else "Supprimé" in message.reply_text.call_args[0][0],
                f"« {texte} » : {'demande le numéro' if reponse else 'supprime'}")
    message = SimpleNamespace(text="supprime ça", reply_text=AsyncMock(),
                              reply_to_message=SimpleNamespace(text=f"Station : Flaine (#{numero + 1})"))
    with SessionLocal() as s:
        cible = s.scalar(select(ChampJournal.id).where(ChampJournal.cle == "station"))
    message.reply_to_message.text = f"Stations : Flaine (#{cible})"
    await bot.supprime(SimpleNamespace(message=message), contexte())
    verifie("Supprimé" in message.reply_text.call_args[0][0], "« supprime ça » en réponse à un message qui cite un seul numéro")
    print("\nTout passe.")


asyncio.run(main())
