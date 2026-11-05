"""Teste la position en direct : cartes OpenStreetMap (jeu de données synthétique), /ou, /rdv, déduction du soir et effacement.
Lancer : .venv/bin/python scripts/test_position.py"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "position.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, commandes_position, horloge, osm, positions  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, ChampJournal, Journee, Personne, Position, Weekend  # noqa: E402

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


DONNEES = {"pistes": [{"nom": "Serpentine", "pts": [[46.000, 6.700], [46.000, 6.710]]}, {"nom": "Diamant noir", "pts": [[46.010, 6.700], [46.010, 6.710]]}],
           "poi": [{"nom": "Restaurant du Col", "type": "restaurant", "lat": 46.0005, "lon": 6.705},
                   {"nom": "Gare de Tête", "type": "remontée", "lat": 46.005, "lon": 6.700},
                   {"nom": "Chalet lointain", "type": "restaurant", "lat": 46.05, "lon": 6.80}]}

# --- Cartes ---------------------------------------------------------------------------------------------------
verifie(760 <= osm.distance_m(46.0, 6.70, 46.0, 6.71) <= 790, "distance : 0,01° de longitude à 46° vaut environ 770 m")
verifie(osm.piste_proche(46.00018, 6.705, donnees=DONNEES) == "Serpentine", "à 20 m d'une piste : reconnue")
verifie(osm.piste_proche(46.00055, 6.705, donnees=DONNEES) is None, "à 60 m : rien, la précision d'un téléphone ne permet pas plus")
verifie(osm.piste_proche(46.0098, 6.702, donnees=DONNEES) == "Diamant noir", "entre deux pistes : la plus proche")
verifie(osm.piste_proche(46.0, 6.7201, donnees=DONNEES) is None, "au-delà du bout d'une piste : rien")

appels = []
def requete():
    appels.append(1)
    return DONNEES
fichier = Path(tempfile.mkdtemp()) / "osm.json"
osm.DONNEES = None
verifie(osm.charger(fichier, requete) == DONNEES and len(appels) == 1 and fichier.exists(), "cartes : premier appel à OpenStreetMap, résultat gardé dans un fichier")
osm.DONNEES = None
osm.charger(fichier, requete)
verifie(len(appels) == 1, "cartes : un fichier de moins d'un mois évite tout nouvel appel")
osm.DONNEES = None
vieux = time.time() - 31 * 24 * 3600
os.utime(fichier, (vieux, vieux))
osm.charger(fichier, requete)
verifie(len(appels) == 2, "cartes : un fichier de plus d'un mois est redemandé")
osm.DONNEES = DONNEES

# --- Enregistrement --------------------------------------------------------------------------------------------------
a(2026, 12, 12, 10, 0)
with SessionLocal() as s:
    maxime, adr = (s.scalar(select(Personne).where(Personne.prenom == p)) for p in ("Maxime", "Adrien"))
    verifie(positions.enregistrer_position(s, maxime, 46.0001, 6.7005, 12.0), "première position gardée")
    j = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    verifie(j is not None, "partager une position crée la journée du jour (vaut présence)")
    verifie(not positions.enregistrer_position(s, maxime, 46.00011, 6.70051, 12.0), "même endroit moins de 2 minutes plus tard : pas gardée")
    verifie(positions.enregistrer_position(s, maxime, 46.00018, 6.705, 12.0), "plus de 20 m plus loin : gardée")
    a(2026, 12, 12, 10, 3)
    verifie(positions.enregistrer_position(s, maxime, 46.00018, 6.705, 12.0), "même endroit mais 3 minutes plus tard : gardée")

    # --- /ou -----------------------------------------------------------------------------------------------------------
    verifie("Je ne connais pas" in positions.ou(s, "Zébulon", DONNEES)[0], "/ou d'un inconnu")
    verifie("Pas de position pour Adrien" in positions.ou(s, "adrien", DONNEES)[0], "/ou d'une personne sans position : on explique comment partager")
    texte, pins = positions.ou(s, None, DONNEES)
    verifie("Maxime" in texte and "secteur Flaine" in texte and "sur ou près de la piste Serpentine" in texte and "à l'instant" in texte, "/ou : secteur, piste et ancienneté")
    verifie(len(pins) == 1 and pins[0][0] == "Maxime", "/ou : une épingle par personne localisée")
    a(2026, 12, 12, 11, 30)
    verifie("position ancienne" in positions.ou(s, "Maxime", DONNEES)[0] and "il y a 87 min" in positions.ou(s, "Maxime", DONNEES)[0], "/ou : une position de plus de 30 minutes est signalée ancienne")
    verifie("Personne ne partage" in positions.ou(s, None, DONNEES)[0], "/ou sans prénom : plus personne de frais")
    positions.enregistrer_position(s, adr, 46.0, 6.7201, 300.0)
    verifie("sur ou près" not in positions.ou(s, "Adrien", DONNEES)[0], "précision de 300 m : on ne devine pas la piste")
    positions.enregistrer_position(s, adr, 45.98, 6.80, 10.0)
    verifie("hors des cinq stations" in positions.ou(s, "Adrien", DONNEES)[0], "à plus de 4 km de tout village : « hors des cinq stations »")
    osm.charger = lambda *a, **k: (_ for _ in ()).throw(OSError("réseau"))
    verifie("secteur Flaine" in positions.ou(s, "Maxime", None)[0], "carte indisponible : on répond quand même avec le secteur")
    osm.charger = lambda *a, **k: DONNEES

    # --- /rdv -----------------------------------------------------------------------------------------------------------
    a(2026, 12, 12, 12, 10)  # la position d'Adrien (11 h 30) a plus de 30 minutes : elle ne compte pas
    positions.enregistrer_position(s, maxime, 46.000, 6.702, 10.0)
    verifie("au moins deux personnes" in positions.rdv(s, maxime, DONNEES)[0], "/rdv avec une seule personne : refus clair")
    positions.enregistrer_position(s, adr, 46.010, 6.708, 10.0)
    texte, pin = positions.rdv(s, maxime, DONNEES)
    verifie("Gare de Tête (remontée)" in texte and pin == (46.005, 6.700), "/rdv : le lieu qui minimise la distance du plus éloigné")
    verifie("à vol d'oiseau" in texte and "Ni les pistes ni les remontées" in texte, "/rdv : la limite est dite")
    verifie("Chalet lointain" not in texte, "/rdv : un lieu lointain n'est pas retenu")
    verifie(s.scalar(select(amis.Reglage.valeur).where(amis.Reglage.cle == f"rdv_count:{maxime.id}")) == "1", "/rdv : compteur pour le trophée Rendez-vous")
    positions.enregistrer_position(s, adr, 46.05, 6.80, 10.0)
    texte, pin = positions.rdv(s, maxime, DONNEES)
    verifie("milieu du groupe" in texte and pin is not None and "3 km" in texte, "/rdv : personnes trop éloignées, on propose le milieu du groupe")
    texte, pin = positions.rdv(s, maxime, {"pistes": [], "poi": []})
    verifie("milieu du groupe" in texte, "/rdv : aucune donnée de carte, on propose le milieu du groupe")

    # --- Déduction du soir ---------------------------------------------------------------------------------------------------
    s.query(Position).delete()
    s.commit()
    for k in range(4):
        positions.enregistrer_position(s, maxime, 46.00018 + k * 0.0003, 6.7005 + k * 0.001, 10.0)  # le long de Serpentine, secteur Flaine
    positions.enregistrer_position(s, maxime, 46.0098, 6.702, 10.0)
    positions.enregistrer_position(s, maxime, 46.0, 6.703, 400.0)  # trop vague : ignorée
    s.add(ChampJournal(journee_id=j.id, cle="piste", valeur="Serpentine", phrase_source="vocal"))
    s.commit()
    ajoutes = positions.deduire_journee(s, j, DONNEES)
    verifie("Diamant noir" in ajoutes and "Serpentine" not in ajoutes, "déduction : nouvelle piste ajoutée, piste déjà au journal non dupliquée")
    verifie("Flaine" in ajoutes, "déduction : station ajoutée quand au moins trois positions y sont")
    verifie(positions.deduire_journee(s, j, DONNEES) == [], "déduction rejouée : aucun doublon")
    verifie(s.scalar(select(ChampJournal).where(ChampJournal.valeur == "Diamant noir")).phrase_source == "position (OpenStreetMap)", "la source est indiquée au journal")

    # --- Effacement après le week-end ---------------------------------------------------------------------------------------------
    w = s.get(Weekend, j.weekend_id)
    w.date_fin = date(2026, 12, 13)
    s.commit()
    a(2026, 12, 12, 20, 0)
    positions.maintenance(s, DONNEES)
    verifie(s.query(Position).count() > 0, "jour même avant 21 h : rien n'est déduit ni effacé")
    a(2026, 12, 12, 21, 5)
    positions.maintenance(s, DONNEES)
    verifie(s.query(Position).count() > 0 and amis.reglage(s, f"deduit:{j.id}") == "1", "jour même après 21 h : déduit, mais les positions restent pour le week-end")
    a(2026, 12, 14, 8, 0)
    positions.maintenance(s, DONNEES)
    verifie(s.query(Position).count() == 0, "week-end terminé : les positions brutes sont effacées")
    s.add(Position(personne_id=maxime.id, journee_id=None, lat=46.0, lon=6.7, recue_a=horloge.maintenant() - timedelta(days=4)))
    s.commit()
    positions.maintenance(s, DONNEES)
    verifie(s.query(Position).count() == 0, "filet de sécurité : jamais plus de trois jours")

# --- Couche Telegram ------------------------------------------------------------------------------------------------------------
async def telegram():
    a(2026, 12, 20, 10, 0)
    osm.DONNEES = DONNEES
    def loc(uid=1, modifie=False):
        message = SimpleNamespace(location=SimpleNamespace(latitude=46.0001, longitude=6.7005, horizontal_accuracy=15.0), reply_text=AsyncMock())
        return SimpleNamespace(effective_message=message, edited_message=message if modifie else None, effective_user=SimpleNamespace(id=uid)), message
    up, msg = loc()
    await commandes_position.position_recue(up, SimpleNamespace())
    verifie("Position reçue" in msg.reply_text.call_args[0][0], "début d'un partage : un seul accusé de réception")
    up, msg = loc(modifie=True)
    a(2026, 12, 20, 10, 5)
    await commandes_position.position_recue(up, SimpleNamespace())
    verifie(not msg.reply_text.await_count, "mise à jour d'un partage en direct (message modifié) : prise en compte, sans message")
    verifie(positions_count() == 2, "…et la position est bien gardée")
    up = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    await commandes_position.commande_position(up, SimpleNamespace())
    verifie("effacées une fois le week-end terminé" in up.message.reply_text.call_args[0][0], "/position explique le partage et l'effacement")
    ctx = SimpleNamespace(args=[], bot=SimpleNamespace(send_location=AsyncMock()))
    up = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=77), effective_user=SimpleNamespace(id=1))
    await commandes_position.commande_ou(up, ctx)
    verifie("Maxime" in up.message.reply_text.call_args[0][0] and ctx.bot.send_location.await_count == 1, "/ou : texte et épingle")
    await commandes_position.commande_rdv(up, ctx)
    verifie("au moins deux personnes" in up.message.reply_text.call_args[0][0] and ctx.bot.send_location.await_count == 1, "/rdv sans assez de monde : pas d'épingle")


def positions_count() -> int:
    with SessionLocal() as s:
        return s.query(Position).count()


asyncio.run(telegram())
print("\nTout passe.")
