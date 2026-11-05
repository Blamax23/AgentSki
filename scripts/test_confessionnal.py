"""Teste le plan de confessionnal : propositions, verdict de la veille, boutons, plan envoyé à l'heure du soir, repères du /kit.
Base jetable, hasard fixé. Lancer : .venv/bin/python scripts/test_confessionnal.py"""
import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "confessionnal.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import confessionnal as cf, horloge, stats  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Confessionnal, Journee, Personne, Presence, Weekend  # noqa: E402

Base.metadata.create_all(engine)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a_heure(jour, h, m=0):
    horloge.regler(horloge.vers_utc(datetime(jour.year, jour.month, jour.day, h, m)))


J1, J2, J3 = date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    pers = {p.prenom: p for p in s.scalars(select(Personne))}
    pers["Adrien"].telegram_id = 2
    w = Weekend(date_debut=J1, date_fin=J3, statut="confirme")
    s.add(w); s.flush()
    jours = {}
    for jour in (J1, J2, J3):
        jr = Journee(weekend_id=w.id, jour=jour, ski=True)
        s.add(jr); s.flush()
        for prenom in ("Maxime", "Adrien", "Achille"):
            s.add(Presence(journee_id=jr.id, personne_id=pers[prenom].id))
        jours[jour] = jr
    s.commit()
    hasard = random.Random(3)

    # --- envoi à l'heure du rappel du soir, à l'organisateur seul, une fois ---
    a_heure(J1, 18, 0)
    verifie(cf.evenements(s, hasard) == [], "plan : rien avant l'heure du rappel du soir")
    a_heure(J1, 19, 0)
    ev = cf.evenements(s, hasard)
    verifie(len(ev) == 1 and ev[0].destinataire == 1 and ev[0].boutons, "plan : un message privé à l'organisateur, avec boutons, à 19 h")
    verifie(cf.evenements(s, hasard) == [], "plan : une seule fois par journée")
    a_heure(date(2026, 12, 31), 19, 0)
    verifie(cf.evenements(s, hasard) == [], "plan : rien le 31, ce n'est pas un jour de ski")

    # --- contenu du plan du 1er janvier ---
    rangees = cf.lignes(s, jours[J1])
    individuelles = [c for c in rangees if c.type != "duo"]
    verifie(sorted(c.personne_id for c in individuelles) == sorted(p.id for p in pers.values() if p.prenom in ("Maxime", "Adrien", "Achille")),
            "plan : chaque présent passe une fois, et personne d'autre")
    verifie(all(c.question == cf.RESOLUTION and c.type == "special" for c in individuelles), "plan du 1er janvier : la résolution de l'année pour tous")
    verifie([c.ordre for c in rangees] == list(range(1, len(rangees) + 1)), "plan : ordre de passage numéroté")
    texte = cf.message(s, jours[J1]).texte
    verifie("proposition" in texte and "à toi de dire" in texte, "plan : présenté comme une proposition, c'est l'organisateur qui décide")

    # --- boutons : fait, annuler, passé, changer ---
    c0 = individuelles[0]
    cf.decider(s, c0.id, "f")
    verifie(s.get(Confessionnal, c0.id).statut == "fait" and s.get(Confessionnal, c0.id).decide_a is not None, "bouton ✅ : marqué fait avec l'heure")
    cf.decider(s, c0.id, "f")
    verifie(s.get(Confessionnal, c0.id).statut == "propose" and s.get(Confessionnal, c0.id).decide_a is None, "bouton ✅ une deuxième fois : annulé")
    cf.decider(s, c0.id, "p")
    verifie(s.get(Confessionnal, c0.id).statut == "passe", "bouton ⏭ : passé")
    cf.decider(s, c0.id, "p")
    c1 = individuelles[1]
    avant = c1.question
    cf.decider(s, c1.id, "q", hasard)
    verifie(s.get(Confessionnal, c1.id).question != avant and s.get(Confessionnal, c1.id).type == "libre", "bouton 🔄 : une autre question, tirée de la banque")
    cf.decider(s, c1.id, "f")
    figee = s.get(Confessionnal, c1.id).question
    cf.decider(s, c1.id, "q", hasard)
    verifie(s.get(Confessionnal, c1.id).question == figee and s.get(Confessionnal, c1.id).statut == "fait", "bouton 🔄 : sans effet sur une ligne déjà faite")
    verifie(cf.decider(s, 99999, "f") is None, "bouton sur une ligne inconnue : sans erreur")

    # --- J2 : prédiction faite la veille => verdict ---
    pred = Confessionnal(journee_id=jours[J1].id, ordre=9, personne_id=pers["Adrien"].id, type="prediction", question=cf.PREDICTION, statut="fait", decide_a=horloge.maintenant())
    s.add(pred); s.commit()
    plan2 = cf.proposer_plan(s, jours[J2], random.Random(1))
    adrien2 = next(c for c in plan2 if c.personne_id == pers["Adrien"].id and c.type != "duo")
    verifie(adrien2.type == "verdict", "verdict : qui a fait une prédiction la veille doit le verdict le lendemain")
    verifie(all(c.type != "verdict" for c in plan2 if c.personne_id != pers["Adrien"].id), "verdict : seulement pour lui")
    verifie(cf.proposer_plan(s, jours[J2]) == plan2, "plan : on ne le recrée pas si il existe déjà")

    # --- J3 : dernier jour => bilan, jamais de prédiction ---
    plan3 = cf.proposer_plan(s, jours[J3], random.Random(2))
    verifie(all(c.type in ("special", "verdict", "duo") for c in plan3) and all(c.type != "prediction" for c in plan3), "dernier jour : un bilan, pas de prédiction pour un lendemain sans ski")

    # --- équité et non-répétition ---
    verifie(cf.proposer_plan(s, jours[J2], random.Random(1)) and True, "plan : relancer ne plante pas")
    libres = [c.question for c in s.scalars(select(Confessionnal).where(Confessionnal.personne_id == pers["Maxime"].id, Confessionnal.type == "libre"))]
    verifie(len(libres) == len(set(libres)), "questions : jamais deux fois la même à la même personne tant que la banque n'est pas épuisée")
    verifie("Adrien : 1 fait" in cf.suivi(s), "suivi : compte ce que chacun a fait")

    # --- repères dans /kit ---
    a_heure(J3, 23, 0)
    resultat = stats.kit(s)
    verifie(resultat is not None and "Confessionnaux" in resultat[1] and "Adrien" in resultat[1], "kit : les confessionnaux faits y sont, avec leur heure")
horloge.regler(None)
print("Tout est bon.")
