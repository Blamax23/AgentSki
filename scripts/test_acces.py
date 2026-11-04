"""Teste le parcours d'accès (question, réponse, choix du prénom, essais limités) avec de faux messages Telegram.
Lancer : .venv/bin/python scripts/test_acces.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "acces.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["QUESTION_ACCES"] = "Question ?"
os.environ["REPONSE_ACCES"] = "Le secret"
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram.ext import ApplicationHandlerStop  # noqa: E402

from agentski import acces, bot  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)


def message(uid, texte):
    msg = SimpleNamespace(text=texte, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid, first_name="Test"), effective_chat=None, message=msg, callback_query=None), msg


def bouton(uid, data):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=50))
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid, first_name="Test"), effective_chat=None, message=None, callback_query=q), q


def contexte():
    return SimpleNamespace(user_data={}, bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900))))


async def passe(update, ctx):
    """True si le bot laisse passer l'update vers les autres commandes."""
    try:
        await bot.liste_blanche(update, ctx)
        return True
    except ApplicationHandlerStop:
        return False


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


async def main():
    verifie(acces.normaliser("  le   Secret ") == "LE SECRET", "comparaison en majuscules, espaces ignorés")
    verifie(acces.reponse_correcte("LE SECRET") and acces.reponse_correcte("le secret"), "réponse juste acceptée")
    verifie(not acces.reponse_correcte("Le secret 2"), "réponse fausse refusée")

    up, _ = message(1, "salut")
    verifie(await passe(up, contexte()), "Maxime (connu) passe directement")

    ctx = contexte()
    up, msg = message(50, "coucou")
    verifie(not await passe(up, ctx) and "Question ?" in msg.reply_text.call_args[0][0], "un inconnu reçoit la question")
    up, msg = message(50, "Les crêtes")
    await passe(up, ctx)
    verifie("2 essais" in msg.reply_text.call_args[0][0], "mauvaise réponse : essais restants")
    up, msg = message(50, "le SECRET")
    await passe(up, ctx)
    verifie("Qui es-tu" in msg.reply_text.call_args[0][0], "bonne réponse en minuscules et majuscules mélangées")
    boutons = [b.text for ligne in msg.reply_text.call_args[1]["reply_markup"].inline_keyboard for b in ligne]
    verifie("Maxime" not in boutons and "Adrien" in boutons, f"seules les personnes libres sont proposées : {boutons}")

    up, q = bouton(50, "acces|Adrien")
    await passe(up, ctx)
    with SessionLocal() as s:
        verifie(acces.personne_connue(s, 50).prenom == "Adrien", "le compte est rattaché à Adrien")
    envoyes = [c.args for c in ctx.bot.send_message.call_args_list]
    verifie(envoyes[0][0] == 1 and "Adrien" in envoyes[0][1], "Maxime est prévenu de l'arrivée")
    verifie("/moment" in envoyes[1][1] and "capsule" in envoyes[2][1].lower(), "l'arrivant reçoit l'aide, puis la première question de la capsule")
    up, _ = message(50, "salut")
    verifie(await passe(up, contexte()), "Adrien passe désormais directement")

    up, q = bouton(60, "acces|Achille")
    await passe(up, contexte())
    with SessionLocal() as s:
        verifie(acces.personne_connue(s, 60) is None, "choisir un prénom sans avoir répondu juste ne donne rien")

    ctx = contexte()
    for texte in ("a", "b", "c", "d"):
        up, msg = message(70, texte)
        await passe(up, ctx)
    up, msg = message(70, "Le secret")
    await passe(up, ctx)
    verifie(not msg.reply_text.await_count, "après 3 échecs, même la bonne réponse est ignorée (blocage)")

    up, q = bouton(51, "acces|Adrien")
    ctx2 = contexte()
    ctx2.user_data["verifie"] = True
    await passe(up, ctx2)
    with SessionLocal() as s:
        verifie(acces.personne_connue(s, 51) is None, "un prénom déjà pris ne peut pas être repris")
    print("\nTout passe.")


asyncio.run(main())
