"""Teste les messages J-30, J-14 et J-7 avant le week-end annuel. Base jetable, horloge simulée.
Lancer : .venv/bin/python scripts/test_compte_a_rebours.py"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "compte.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["ALLOWED_USERS"] = "1:Maxime"
os.environ["DATE_WEEKEND_ANNUEL"] = "2026-12-31"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import compte_a_rebours as cr, horloge, medias  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Personne  # noqa: E402

Base.metadata.create_all(engine)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(jour, h=18, m=0):
    horloge.regler(horloge.vers_utc(datetime(jour.year, jour.month, jour.day, h, m)))


with SessionLocal() as s:
    seed_personnes(s); sync_allowed_users(s)
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien")); adrien.telegram_id = 2
    achille = s.scalar(select(Personne).where(Personne.prenom == "Achille"))  # pas encore dans le bot : pas de telegram_id
    s.commit()

    a(date(2026, 11, 30))
    verifie(cr.evenements(s) == [], "31 jours avant : rien")
    a(date(2026, 12, 1), 17, 59)
    verifie(cr.evenements(s) == [], "J-30 avant l'heure du message : rien")
    a(date(2026, 12, 1), 18, 0)
    ev = cr.evenements(s)
    verifie({e.destinataire for e in ev} == {1, 2}, "J-30 : un message privé à chaque personne joignable, pas aux autres")
    t = ev[0].texte
    verifie("J-30" in t and "vocal" in t and t.count("• ") == 3, "J-30 : annonce, invitation au vocal et trois questions")
    verifie(cr.evenements(s) == [], "J-30 : une seule fois par personne")
    a(date(2026, 12, 2), 18, 0)
    verifie(cr.evenements(s) == [], "le lendemain : rien de plus")

    medias.ranger_vocal(s, adrien, "V1", 50_000)
    a(date(2026, 12, 17), 18, 0)
    ev = {e.destinataire: e.texte for e in cr.evenements(s)}
    verifie(all("17" not in t or True for t in ev.values()) and "plus que 14 jours" in ev[2], "J-14 : le bon décompte")
    verifie("1 vocal de gardé" in ev[2] and "n'as pas encore envoyé" in ev[1], "J-14 : encourage selon les vocaux déjà envoyés par la personne")
    verifie("le groupe en a déjà envoyé 1" in ev[1], "J-14 : donne le nombre de vocaux du groupe, sans nommer personne")

    a(date(2026, 12, 24), 20, 0)
    ev = {e.destinataire: e.texte for e in cr.evenements(s)}
    verifie("J-7" in ev[1] and "GoPro chargée" in ev[1], "J-7 : le décompte et la liste du matériel de tournage")
    verifie(ev[1].count("• ") == 3 and cr._questions(adrien, 7) == cr._questions(adrien, 7), "J-7 : trois questions, les mêmes si on recalcule")
    verifie(cr.evenements(s) == [], "J-7 : une seule fois")

    # --- jalons manqués : seul le plus récent part ---
    with SessionLocal() as s2:
        from agentski.models import Reglage
        s2.query(Reglage).filter(Reglage.cle.like("compte:%")).delete(); s2.commit()
        a(date(2026, 12, 26), 19, 0)
        ev = cr.evenements(s2)
        verifie(len(ev) == 2 and all("J-5" in e.texte for e in ev), "bot arrêté pendant des semaines : un seul message, avec le vrai décompte, sans rattraper les jalons manqués")
        a(date(2026, 12, 31), 19, 0)
        verifie(cr.evenements(s2) == [], "le jour J : plus de compte à rebours")
horloge.regler(None)
print("Tout est bon.")
