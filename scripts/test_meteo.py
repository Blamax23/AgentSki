"""Teste la météo, le point du matin, le briefing du vendredi et le récap, avec de fausses données Open-Meteo.
Lancer : .venv/bin/python scripts/test_meteo.py [--reel]"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "meteo.db")
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, briefing, horloge, meteo, previsions  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Chute, Citation, Defi, Journee, Media, Note, Personne, Reglage  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    s.scalar(select(Personne).where(Personne.prenom == "Adrien")).telegram_id = 2
    s.commit()


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


def faux_appel(station, altitude, jours=4):
    """Réponse au format Open-Meteo, du 10 au 17 décembre 2026 ; plus il fait haut, plus il neige."""
    debut = datetime(2026, 12, 10)
    heures = [debut + timedelta(hours=k) for k in range(8 * 24)]
    haut = altitude >= 1800
    return {"hourly": {"time": [h.strftime("%Y-%m-%dT%H:00") for h in heures],
                       "temperature_2m": [-6.0 if haut else 2.0] * len(heures), "snowfall": [0.25 if haut else 0.0] * len(heures),
                       "snow_depth": [0.40 if haut else 0.0] * len(heures), "wind_speed_10m": [18.0] * len(heures),
                       "wind_gusts_10m": [35.0 if haut else 15.0] * len(heures), "freezing_level_height": [1500.0] * len(heures),
                       "weather_code": [73 if haut else 3] * len(heures)},
            "daily": {"time": [(debut + timedelta(days=k)).strftime("%Y-%m-%d") for k in range(8)],
                      "weather_code": [73 if haut else 3] * 8, "temperature_2m_max": [-3.0 if haut else 4.0] * 8,
                      "temperature_2m_min": [-9.0 if haut else 0.0] * 8, "snowfall_sum": [6.0 if haut else 0.0] * 8,
                      "precipitation_sum": [4.0] * 8, "wind_gusts_10m_max": [40.0 if haut else 20.0] * 8}}


meteo.appeler = faux_appel

# --- Lecture et textes -------------------------------------------------------------------------------
aujourd = date(2026, 12, 10)  # un jeudi
verifie(meteo.trouver_station("flaine") == "Flaine" and meteo.trouver_station("SAMOENS") == "Samoëns", "stations : casse et accents ignorés")
verifie(meteo.trouver_station("Chamonix") is None, "station inconnue : rien")
verifie(meteo.trouver_jour("demain", aujourd) == date(2026, 12, 11) and meteo.trouver_jour("samedi", aujourd) == date(2026, 12, 12), "jours : « demain », « samedi »")
verifie(meteo.trouver_jour("jeudi", aujourd) == aujourd and meteo.trouver_jour("", aujourd) == aujourd, "jours : aujourd'hui par défaut")
verifie(meteo.trouver_jour("12/12", aujourd) == date(2026, 12, 12) and meteo.trouver_jour("2026-12-13", aujourd) == date(2026, 12, 13), "jours : dates écrites")
verifie(meteo.trouver_jour("n'importe quoi", aujourd) is None, "jour incompréhensible : rien")
verifie(previsions._station_et_reste(["Les", "Carroz", "demain"]) == ("Les Carroz", "demain"), "station en deux mots suivie d'un jour")
etat = meteo.etat_actuel(faux_appel("Flaine", 2000), datetime(2026, 12, 12, 10, 0))
verifie(etat["neige_sol_cm"] == 40 and etat["neige_24h"] == 6.0 and etat["isotherme"] == 1500, "état actuel : neige au sol, neige des 24 h, isotherme")
verifie(meteo.resume_jour(faux_appel("Flaine", 2000), date(2027, 1, 1)) is None, "jour hors prévision : rien")
info = meteo.texte_info("Flaine", datetime(2026, 12, 12, 10, 0))
verifie("Flaine" in info and "40 cm à 2000 m" in info and "0 cm au village (1613 m)" in info and "Estimations" in info, "/info : neige au village et en altitude, avec l'avertissement")
verifie("🌨" in meteo.texte_meteo("Flaine", date(2026, 12, 12)) and "-9 à -3 °C" in meteo.texte_meteo("Flaine", date(2026, 12, 12)), "/meteo : température et neige à 2000 m")
verifie("3 jours" in meteo.texte_meteo("Flaine", date(2027, 2, 1)), "/meteo trop loin : message clair")
pm = meteo.texte_point_matin(date(2026, 12, 12))
verifie(all(s in pm for s in meteo.STATIONS_GEO) and pm.count("\n") == 5, "point météo : une ligne par station")

# --- Réglages et avalanche ------------------------------------------------------------------------------
with SessionLocal() as s:
    adr, maxime = s.scalar(select(Personne).where(Personne.prenom == "Adrien")), s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    verifie("désactivé" in briefing.regler_point_meteo(s, adr, []), "/pointmeteo seul : état actuel")
    verifie("07:00" in briefing.regler_point_meteo(s, adr, ["on"]), "/pointmeteo on : 7 h par défaut")
    verifie("06:45" in briefing.regler_point_meteo(s, adr, ["on", "6h45"]) and s.get(Reglage, f"pointmeteo:{adr.id}").valeur == "06:45", "/pointmeteo on 6h45")
    verifie("invalide" in briefing.regler_point_meteo(s, adr, ["on", "minuit"]), "heure invalide refusée")
    verifie("pas encore branché" in briefing.texte_avalanche(s), "/avalanche : honnête sur ce qui n'est pas branché")
    amis.definir_reglage(s, "avalanche_simule", "3")
    verifie("marqué" in briefing.texte_avalanche(s) and "SIMULÉE" in briefing.texte_avalanche(s), "/avalanche simulé : clairement signalé")

    # --- Point météo du matin ---------------------------------------------------------------------------------
    amis.definir_reglage(s, "groupe_id", "-100")
    a(2026, 12, 12, 6, 30)
    verifie(briefing.evenements_meteo(s, lambda *_: "x") == [], "avant l'heure choisie : rien")
    a(2026, 12, 12, 6, 50)
    evs = briefing.evenements_meteo(s, lambda *_: "x")
    verifie(len(evs) == 1 and evs[0].destinataire == 2 and "Point météo" in evs[0].texte, "à l'heure choisie : le point part en privé")
    verifie(briefing.evenements_meteo(s, lambda *_: "x") == [], "…une seule fois par jour")
    a(2027, 7, 14, 7, 0)
    verifie(briefing.evenements_meteo(s, lambda *_: "x") == [], "hors saison : rien, même activé")
    def tombe(*a, **k):
        raise OSError("réseau")
    a(2026, 12, 13, 7, 0)
    meteo.appeler = tombe
    verifie(briefing.evenements_meteo(s, lambda *_: "x") == [], "service météo en panne : rien")
    meteo.appeler = faux_appel
    verifie(len(briefing.evenements_meteo(s, lambda *_: "x")) == 1, "…et réessayé quand il revient")

    # --- Briefing du vendredi ----------------------------------------------------------------------------------
    briefing.regler_point_meteo(s, adr, ["off"])  # le point du matin d'Adrien partirait aussi : on l'éteint pour isoler le briefing
    vus = {}
    def gen(systeme, utilisateur):
        vus["u"] = utilisateur
        return "Privilégiez Flaine samedi."
    a(2026, 12, 11, 18, 5)
    verifie(briefing.evenements_meteo(s, gen) == [], "pas de journée de ski au programme : pas de briefing")
    sam = Journee(weekend_id=1, jour=date(2026, 12, 12))
    from agentski.models import Weekend
    s.add(Weekend(id=1, date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 13), statut="confirme"))
    s.flush()
    s.add_all([sam, Journee(weekend_id=1, jour=date(2026, 12, 13))])
    s.commit()
    a(2026, 12, 11, 17, 55)
    verifie(briefing.evenements_meteo(s, gen) == [], "vendredi avant 18 h : rien")
    a(2026, 12, 11, 18, 5)
    evs = briefing.evenements_meteo(s, gen)
    verifie(len(evs) == 1 and evs[0].destinataire == "groupe" and evs[0].texte.startswith("📋"), "vendredi 18 h : briefing au groupe")
    verifie("Flaine" in vus["u"] and "Défis possibles" in vus["u"] and "rafales" in vus["u"], "le modèle reçoit les prévisions des stations et deux défis")
    verifie(vus["u"].count("\n- ") == 2 and all(" : " in l for l in vus["u"].split("Défis possibles :")[1].strip().splitlines()), "…chaque défi proposé arrive avec sa description exacte")
    verifie(briefing.evenements_meteo(s, gen) == [], "…une seule fois")
    s.delete(s.get(Reglage, "briefing:2026-12-11"))
    s.commit()
    verifie(briefing.evenements_meteo(s, lambda *_: (_ for _ in ()).throw(RuntimeError("x"))) == [], "modèle en panne : pas de briefing, réessayé ensuite")
    verifie(len(briefing.evenements_meteo(s, gen)) == 1, "…réessayé et envoyé")

    # --- Récap du week-end ------------------------------------------------------------------------------------------
    a(2026, 12, 13, 20, 20)
    verifie(not any("Récap" in e.texte or "📊" in e.texte for e in briefing.evenements_meteo(s, gen)), "avant 20 h 30 le dernier soir : pas de récap")
    verifie(briefing.faits_recap(s, s.get(Weekend, 1)) == ["2 jours de ski"], "sans aucune donnée : seulement les jours, rien d'inventé")
    for prenom in ("Maxime", "Adrien"):
        amis._presence(s, s.get(Journee, 1), s.scalar(select(Personne).where(Personne.prenom == prenom)))
    s.add_all([Chute(journee_id=1, personne_id=adr.id), Chute(journee_id=1, personne_id=adr.id), Chute(journee_id=2, personne_id=maxime.id),
               Citation(journee_id=1, personne_id=adr.id, texte="C'est une verte non ?"), Media(personne_id=adr.id, journee_id=1, type="photo", file_id="F"),
               Note(journee_id=1, personne_id=adr.id, note=8), Defi(type="gage", journee_id=1, contenu="x")])
    s.commit()
    faits = briefing.faits_recap(s, s.get(Weekend, 1))
    verifie(any("chutes : 3 au total (Adrien 2, Maxime 1)" in f for f in faits), "récap : les chutes par personne")
    verifie(any("1.5 chute par jour de ski" in f for f in faits) and any("1.5 chute par personne présente" in f for f in faits), "récap : les rapports sont calculés par le code, pas par le modèle")
    verifie(any("citation" in f and "verte" in f for f in faits) and any("note moyenne" in f and "8.0" in f for f in faits), "récap : citations et notes")
    verifie(any("Le gage" in f for f in faits) and any("1 photo" in f for f in faits), "récap : défis et médias")
    verifie(not any("pistes" in f or "stations" in f for f in faits), "une stat manquante n'apparaît pas")
    a(2026, 12, 13, 20, 40)
    evs = briefing.evenements_meteo(s, lambda syst, user: "Récap : " + ("chutes" if "chutes" in user else ""))
    verifie(any(e.texte.startswith("📊 Récap") for e in evs), "dernier soir à 20 h 30 : récap au groupe")
    verifie(not any("📊" in e.texte for e in briefing.evenements_meteo(s, gen)), "…une seule fois")
    verifie("Récap du week-end" in briefing.recap(s, s.get(Weekend, 1), lambda *_: (_ for _ in ()).throw(RuntimeError("x"))), "modèle en panne : les faits bruts")

# --- Couche Telegram ---------------------------------------------------------------------------------------------
def maj(texte=None, args=()):
    msg = SimpleNamespace(text=texte, reply_text=AsyncMock())
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1)), SimpleNamespace(args=list(args))


async def telegram():
    a(2026, 12, 12, 10, 0)
    up, ctx = maj()
    await previsions.commande_info(up, ctx)
    verifie(len(up.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard) == 5, "/info sans station : cinq boutons")
    up, ctx = maj(args=["Flaine"])
    await previsions.commande_info(up, ctx)
    verifie("conditions maintenant" in up.message.reply_text.call_args[0][0], "/info Flaine")
    up, ctx = maj(args=["Les", "Carroz", "samedi"])
    await previsions.commande_meteo(up, ctx)
    verifie("Les Carroz" in up.message.reply_text.call_args[0][0] and "samedi" in up.message.reply_text.call_args[0][0], "/meteo Les Carroz samedi")
    up, ctx = maj(args=["Chamonix"])
    await previsions.commande_meteo(up, ctx)
    verifie("Format" in up.message.reply_text.call_args[0][0], "/meteo station inconnue : le format")
    up, ctx = maj(args=["Flaine", "peut-être"])
    await previsions.commande_meteo(up, ctx)
    verifie("Quel jour" in up.message.reply_text.call_args[0][0], "/meteo jour incompréhensible")
    meteo.appeler = tombe
    up, ctx = maj(args=["Flaine"])
    await previsions.commande_info(up, ctx)
    verifie("n'arrive pas à joindre" in up.message.reply_text.call_args[0][0], "service en panne : message clair, pas d'erreur")
    meteo.appeler = faux_appel
    up, ctx = maj()
    await previsions.commande_avalanche(up, ctx)
    verifie("marqué" in up.message.reply_text.call_args[0][0], "/avalanche")
    up, ctx = maj(args=["on", "7:30"])
    await previsions.commande_pointmeteo(up, ctx)
    verifie("07:30" in up.message.reply_text.call_args[0][0], "/pointmeteo")

asyncio.run(telegram())

if "--reel" in sys.argv:
    importlib = __import__("importlib")
    importlib.reload(meteo)
    print("\n--- Open-Meteo réel ---")
    print(meteo.texte_info("Flaine", datetime.now().replace(minute=0, second=0, microsecond=0)))
print("\nTout passe.")
