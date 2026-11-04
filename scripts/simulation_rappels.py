"""Simulation de deux week-ends avec une horloge accélérée, minute par minute, sur une base jetable.

Scénario : 12-13 déc. Maxime + Adrien ; 19-20 déc. avec Achille (première venue) ;
coupures du bot le sam. 19 (7 h-10 h) et le dim. 20 (7 h-8 h) ; week-end du 26-27 refusé ;
question du 31 sans réponse (relances toutes les 3 h) puis réponse le vendredi 1er à 14 h.
Lancer : .venv/bin/python scripts/simulation_rappels.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "simulation.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import horloge, rappels  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes  # noqa: E402
from agentski.models import Base, Descente, Journee, Personne, Rappel, Sequence  # noqa: E402

Base.metadata.create_all(engine)
DEBUT, FIN = datetime(2026, 12, 9, 12, 0), datetime(2027, 1, 4, 12, 0)  # heure de Paris
COUPURES = [(datetime(2026, 12, 19, 7, 0), datetime(2026, 12, 19, 10, 0)),
            (datetime(2026, 12, 20, 7, 0), datetime(2026, 12, 20, 8, 0))]
# Réponses simulées à la question du jeudi : (jours cochés, invités)
REPONSES = {"2026-12-10": (["Samedi", "Dimanche"], ["Adrien"]),
            "2026-12-17": (["Samedi", "Dimanche"], ["Adrien", "Achille"]),
            "2026-12-24": None,
            "2026-12-31": (["Samedi", "Dimanche"], ["Adrien"])}
# Réponses tardives : on ne répond qu'à partir de cette heure (les relances partent en attendant)
REPONDRE_APRES = {"2026-12-31": datetime(2027, 1, 1, 14, 0)}


def cliquer(session, message, libelle):
    data = next(d for ligne in message.boutons for t, d in ligne if t.removeprefix("✅ ") == libelle)
    return rappels.repondre(session, data)


def repondre_au_jeudi(session, message, jeudi):
    reponse = REPONSES.get(jeudi)
    if reponse is None:
        return rappels.repondre(session, next(d for t, d in message.boutons[1] if t.startswith("Non")))
    jours, invites = reponse
    for j in jours:
        message = cliquer(session, message, j)
    message = cliquer(session, message, "Valider")
    for p in invites:
        message = cliquer(session, message, p)
    return cliquer(session, message, "Valider")


def ajouter_historique_pov(session):
    """Après le 1er week-end : 7 descentes filmées par Maxime, 3 par Adrien."""
    j = session.scalar(select(Journee).where(Journee.jour == datetime(2026, 12, 12).date()))
    seq = Sequence(journee_id=j.id, lieu="Flaine")
    session.add(seq)
    session.flush()
    for prenom, n in (("Maxime", 7), ("Adrien", 3)):
        pid = session.scalar(select(Personne.id).where(Personne.prenom == prenom))
        session.add_all(Descente(sequence_id=seq.id, filmeur_id=pid) for _ in range(n))
    session.commit()


with SessionLocal() as session:
    seed_personnes(session)
    local, envoyes, historique = DEBUT, 0, False
    while local < FIN:
        horloge.regler(horloge.vers_utc(local))
        if any(a <= local < b for a, b in COUPURES):
            local += timedelta(minutes=1)
            continue
        if not historique and local >= datetime(2026, 12, 14):
            ajouter_historique_pov(session)
            historique = True
        rappels.planifier_jeudi(session)
        for envoi in rappels.a_envoyer(session):
            rappels.marquer_envoye(session, envoi.rappel_id)
            envoyes += 1
            print(f"\n[{local:%a %d/%m %H:%M}] → Maxime\n{envoi.message.texte}")
            if envoi.message.boutons:
                jeudi = (local.date() - timedelta(days=local.weekday() - 3)).isoformat()
                if local >= REPONDRE_APRES.get(jeudi, datetime.min):
                    print(f"  (réponse simulée) {repondre_au_jeudi(session, envoi.message, jeudi).texte}")
                else:
                    print("  (aucune réponse)")
        local += timedelta(minutes=1)

    abandons = session.scalars(select(Rappel).where(Rappel.abandonne_a.is_not(None))).all()
    print(f"\n=== {envoyes} messages envoyés, {len(abandons)} rappels abandonnés :")
    for r in abandons:
        print(f"  {r.type} prévu {horloge.vers_paris(r.envoyer_a):%a %d/%m %H:%M}")
