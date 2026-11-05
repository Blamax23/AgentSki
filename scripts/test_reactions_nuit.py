"""Teste les réactions-emoji et le bonne nuit personnalisé. Modèle simulé, base jetable.
Lancer : .venv/bin/python scripts/test_reactions_nuit.py"""
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "reactions.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402
from telegram.constants import ReactionEmoji  # noqa: E402

from agentski import bonnenuit, horloge, reactions  # noqa: E402
from agentski.amis import aujourdhui  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Chute, Journee, Personne, Presence, Reglage, Weekend  # noqa: E402

Base.metadata.create_all(engine)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


autorises = {e.value for e in ReactionEmoji}
tous = {e for _, choix in reactions.MOTS for e in choix} | set(reactions.AU_HASARD)
verifie(tous <= autorises, f"réactions : tous les emojis sont autorisés par Telegram ({sorted(tous - autorises)})")
verifie("🧀" not in tous, "réactions : pas de 🧀, que Telegram refuse")

with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    adrien.telegram_id = 2
    s.commit()

    toujours = random.Random(); toujours.random = lambda: 0.0
    jamais = random.Random(); jamais.random = lambda: 0.99
    verifie(reactions.choisir(s, "ce soir raclette ?", toujours) in autorises, "mot-clé : une réaction autorisée quand le sort le veut")
    verifie(reactions.choisir(s, "ce soir raclette ?", jamais) is None, "mot-clé : pas de réaction quand le sort ne veut pas")
    verifie(reactions.choisir(s, "On se retrouve à 8h", jamais) is None, "message banal : pas de réaction la plupart du temps")
    verifie(reactions.choisir(s, "On se retrouve à 8h", toujours) in autorises, "message banal : réaction rare possible")
    for _ in range(30):
        reactions.choisir(s, "raclette", toujours)
    verifie(reactions.choisir(s, "raclette", toujours) is None, f"plafond : plus de réaction après {reactions.PLAFOND_PAR_JOUR} par jour")

    # --- bonne nuit ---
    w = Weekend(date_debut=aujourdhui(), date_fin=aujourdhui() + timedelta(days=1), statut="confirme")
    s.add(w); s.flush()
    j = Journee(weekend_id=w.id, jour=aujourdhui(), ski=True)
    s.add(j); s.flush()
    s.add_all([Presence(journee_id=j.id, personne_id=maxime.id), Presence(journee_id=j.id, personne_id=adrien.id), Chute(journee_id=j.id, personne_id=adrien.id)])
    s.commit()

    vus = []
    def generateur(systeme, demande):
        vus.append(demande)
        return f"Bonne nuit perso ({len(vus)})"

    def a_heure(h, m):
        horloge.regler(horloge.vers_utc(datetime.combine(aujourdhui(), datetime.min.time()).replace(hour=h, minute=m)))
    a_heure(21, 0)
    verifie(bonnenuit.evenements(s, generateur) == [], "bonne nuit : rien avant 22h30")
    a_heure(22, 45)
    ev = bonnenuit.evenements(s, generateur)
    verifie({e.destinataire for e in ev} == {1, 2}, "bonne nuit : un message privé à chaque personne présente")
    verifie(any("1 chute" in v and "Adrien" in v for v in vus), "bonne nuit : s'appuie sur les faits de la journée de la personne")
    verifie(bonnenuit.evenements(s, generateur) == [], "bonne nuit : une seule fois par personne et par jour")
    horloge.regler(None)

    # --- modèle en panne : texte de secours ---
    s.execute(delete(Reglage).where(Reglage.cle.like("bonnenuit:%")))
    s.commit()
    a_heure(23, 0)
    def casse(systeme, demande):
        raise RuntimeError("panne")
    ev = bonnenuit.evenements(s, casse)
    verifie(len(ev) == 2 and any("Adrien" in e.texte for e in ev), "bonne nuit : si le modèle tombe, un texte de secours part quand même")
    horloge.regler(None)

print("Tout est bon.")
