"""Teste les règles de commandes : où (privé, groupe) et quand (week-end de ski) chaque commande est permise.
Lancer : .venv/bin/python scripts/test_regles.py"""
import asyncio
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "regles.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from sqlalchemy import select  # noqa: E402
from telegram.ext import ApplicationHandlerStop  # noqa: E402

from agentski import horloge, regles  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Personne, Weekend  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    s.scalar(select(Personne).where(Personne.prenom == "Adrien")).telegram_id = 2
    s.add_all([Weekend(date_debut=date(2026, 12, 11), date_fin=date(2026, 12, 13), statut="confirme"),
               Weekend(date_debut=date(2026, 12, 18), date_fin=date(2026, 12, 20), statut="annule")])
    s.commit()


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


# --- Le texte d'une commande ----------------------------------------------------------------------------------
def msg(texte):
    return SimpleNamespace(message=SimpleNamespace(text=texte))
verifie([regles.commande_de(msg(t)) for t in ("/duel Adrien Achille", "/Duel@krok_bot x", "/moment", "/chute@krok_bot")] == ["duel", "duel", "moment", "chute"], "commande : arguments, casse et suffixe @bot ignorés")
verifie(regles.commande_de(msg("salut")) is None and regles.commande_de(msg("")) is None and regles.commande_de(SimpleNamespace(message=None)) is None, "pas une commande : message ordinaire, vide ou modifié")

# --- Le week-end de ski ----------------------------------------------------------------------------------------
with SessionLocal() as s:
    verifie(regles.en_week_end_de_ski(s, date(2026, 12, 11)) and regles.en_week_end_de_ski(s, date(2026, 12, 13)), "week-end de ski : les bornes comptent (vendredi et dimanche)")
    verifie(not regles.en_week_end_de_ski(s, date(2026, 12, 10)) and not regles.en_week_end_de_ski(s, date(2026, 12, 14)), "hors du week-end : non")
    verifie(not regles.en_week_end_de_ski(s, date(2026, 12, 19)), "un week-end annulé n'est pas un week-end de ski")

# --- La phrase de refus ------------------------------------------------------------------------------------------
R = regles
verifie(R.refus(R.PRIVE, prive=False, ski=True, organisateur=False) is not None and R.refus(R.PRIVE, prive=True, ski=True, organisateur=False) is None, "privé seulement : refusé dans le groupe, permis en privé")
verifie(R.refus(R.GROUPE, prive=True, ski=True, organisateur=False) is not None and R.refus(R.GROUPE, prive=False, ski=True, organisateur=False) is None, "groupe seulement : refusé en privé, permis dans le groupe")
verifie(R.refus(R.SKI, prive=True, ski=False, organisateur=False) is not None and R.refus(R.SKI, prive=False, ski=True, organisateur=False) is None, "ski seulement : refusé hors week-end, permis pendant")
verifie(R.refus(R.Regle(qui="organisateur"), True, True, False) is not None and R.refus(R.Regle(qui="organisateur"), True, True, True) is None, "organisateur seulement")
verifie(R.refus(R.Regle(), True, False, False) is None and R.refus(R.Regle(), False, False, False) is None, "sans règle : permis partout, toujours")
verifie("privé" in R.refus(R.PRIVE, False, True, False) and "groupe" in R.refus(R.GROUPE, True, True, False) and "week-end de ski" in R.refus(R.SKI, True, False, False), "chaque refus dit pourquoi")

# --- Le garde-fou ------------------------------------------------------------------------------------------------
async def essayer(texte, uid=1, chat="private"):
    up = SimpleNamespace(message=SimpleNamespace(text=texte, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=uid), effective_chat=SimpleNamespace(type=chat))
    try:
        await regles.garde(up, SimpleNamespace())
        return True, up
    except ApplicationHandlerStop:
        return False, up


async def scenarios():
    a(2026, 12, 12, 10, 0)  # un samedi de week-end de ski
    ok, up = await essayer("/easter", chat="group")
    verifie(not ok and "en privé" in up.message.reply_text.call_args[0][0], "garde-fou : /easter dans le groupe refusé avec explication, la commande ne part pas")
    verifie((await essayer("/easter", chat="private"))[0], "garde-fou : /easter en privé permis")
    verifie(not (await essayer("/duel Adrien Achille", chat="private"))[0] and (await essayer("/duel Adrien Achille", chat="supergroup"))[0], "garde-fou : /duel refusé en privé, permis dans un supergroupe")
    verifie((await essayer("/moment bonjour"))[0], "garde-fou : /moment permis un samedi de week-end de ski")
    a(2026, 12, 16, 10, 0)  # un mercredi sans week-end
    ok, up = await essayer("/moment bonjour")
    verifie(not ok and "week-end de ski" in up.message.reply_text.call_args[0][0], "garde-fou : /moment refusé en semaine, avec explication")
    verifie(not (await essayer("/chute"))[0] and not (await essayer("/ou"))[0] and not (await essayer("/citation Adrien bonjour"))[0], "garde-fou : chute, ou et citation (matière du vlog) aussi")
    verifie((await essayer("/play titre"))[0] and (await essayer("/son Daft Punk"))[0] and (await essayer("/stats"))[0], "garde-fou : la musique et les stats restent permises en semaine")
    verifie(not (await essayer("/groupe", uid=2, chat="group"))[0] and (await essayer("/groupe", uid=1, chat="group"))[0], "garde-fou : /groupe réservé à l'organisateur")
    verifie((await essayer("/inconnue"))[0] and (await essayer("bonjour"))[0], "garde-fou : une commande sans règle et un texte ordinaire ne sont jamais arrêtés")
    ok, up = await essayer("/Moment@krok_bot salut")
    verifie(not ok, "garde-fou : la règle s'applique aussi avec la casse et le suffixe @bot")
    up = SimpleNamespace(message=None, edited_message=SimpleNamespace(), effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(type="private"))
    await regles.garde(up, SimpleNamespace())
    verifie(True, "garde-fou : un message modifié (position en direct) passe sans erreur")

asyncio.run(scenarios())

# --- La table elle-même ---------------------------------------------------------------------------------------------
noms = set()
for fichier in (RACINE / "agentski").glob("*.py"):
    texte = fichier.read_text(encoding="utf-8")
    noms |= set(re.findall(r'CommandHandler\("([a-z_]+)"', texte)) | set(re.findall(r'\("([a-z_]+)", (?:commande|bouton)_', texte))
inconnues = [c for c in regles.REGLES if c not in noms]
verifie(not inconnues or set(inconnues) <= set(), f"table : chaque commande de la table existe (à venir : {sorted(set(inconnues))})")
print("\nTout passe.")
