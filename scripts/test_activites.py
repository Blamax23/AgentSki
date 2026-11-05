"""Teste l'analyse des fichiers FIT et GPX : descentes, dénivelé, vitesse, doublons, confidentialité. Traces synthétiques, base jetable.
Lancer : .venv/bin/python scripts/test_activites.py   (les tests FIT demandent `pip install -r requirements-dev.txt`)"""
import asyncio
import json
import os
import random
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "activites.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import activites, briefing, commandes_stats, horloge, stats  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Activite, Base, Journee, Personne, Weekend  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)

try:
    import fit_tool  # noqa: F401
    FIT = True
except ImportError:
    FIT = False


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


DEBUT = datetime(2026, 12, 12, 8, 0)  # UTC, soit 9 h à Paris


def trace(descentes=4, bruit=0.0, remontee_m=400, graine=1, pas=5):
    """Remontée de 6 min (400 m), puis descente de 3 min (400 m à 54 km/h), répétées ; (t, lat, lon, alt, vitesse m/s)."""
    hasard = random.Random(graine)
    t, lat, lon, alt, lignes = DEBUT, 46.0, 6.69, 1600.0, []
    for _ in range(descentes):
        for _ in range(72):  # remontée
            t += timedelta(seconds=pas)
            alt += remontee_m / 72
            lat += 0.00004
            lon -= 0.00015
            lignes.append((t, lat, lon, alt + hasard.gauss(0, bruit), 2.3))
        for _ in range(36):  # descente à 15 m/s
            t += timedelta(seconds=pas)
            alt -= remontee_m / 36
            lon += 0.00097
            lignes.append((t, lat, lon, alt + hasard.gauss(0, bruit), 15.0))
    return lignes


def gpx(lignes, avec_altitude=True) -> bytes:
    pts = "".join(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}">' + (f"<ele>{al:.1f}</ele>" if avec_altitude else "") +
                  f'<time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>' for t, la, lo, al, _ in lignes)
    return f'<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>{pts}</trkseg></trk></gpx>'.encode()


def fit(lignes) -> bytes:
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.profile_type import FileType, Manufacturer
    b = FitFileBuilder(auto_define=True, min_string_size=50)
    f = FileIdMessage()
    f.type, f.manufacturer, f.product, f.serial_number = FileType.ACTIVITY, Manufacturer.GARMIN.value, 1, 1
    f.time_created = int(DEBUT.replace(tzinfo=timezone.utc).timestamp() * 1000)
    b.add(f)
    for t, la, lo, al, v in lignes:
        r = RecordMessage()
        r.timestamp, r.position_lat, r.position_long, r.altitude, r.speed = int(t.replace(tzinfo=timezone.utc).timestamp() * 1000), la, lo, al, v
        b.add(r)
    chemin = Path(tempfile.mkdtemp()) / "t.fit"
    b.build().to_file(str(chemin))
    return chemin.read_bytes()


# --- Lecture ---------------------------------------------------------------------------------------------------
points = activites.lire_gpx(gpx(trace()))
verifie(len(points) == 432 and abs(points[0].alt - (1600 + 400 / 72)) < 0.2 and points[0].t == DEBUT + timedelta(seconds=5), "GPX : points, altitude et heure UTC lus")
verifie(activites.lire_gpx(gpx(trace(), avec_altitude=False)) == [], "GPX sans altitude : aucun point exploitable")
if FIT:
    pf = activites.lire_fit(fit(trace()))
    verifie(len(pf) == 432 and abs(pf[0].lat - 46.00004) < 1e-5 and abs(pf[0].lon - 6.68985) < 1e-5 and abs(pf[0].alt - (1600 + 400 / 72)) < 0.5, "FIT : points lus, positions converties des semicircles en degrés")
    verifie(pf[0].v is not None and abs(pf[0].v - 2.3) < 0.05 and pf[0].t == DEBUT + timedelta(seconds=5), "FIT : vitesse de l'appareil et heure UTC lues")

# --- Analyse -----------------------------------------------------------------------------------------------------
r = activites.analyser(points)
verifie(len(r.descentes) == 4, "4 remontées et 4 descentes : 4 descentes comptées")
verifie(all(392 <= d.chute_m <= 402 for d in r.descentes) and 1570 <= r.denivele_neg <= 1610, f"dénivelé négatif à 2 % près : {r.denivele_neg} m pour 1 600 m de descente réelle")
verifie(52 <= r.vitesse_max_kmh <= 56, f"vitesse max cohérente avec 15 m/s : {r.vitesse_max_kmh} km/h (calculée depuis les positions, le GPX n'en donne pas)")
verifie(r.descentes[0].debut == DEBUT + timedelta(minutes=6) - timedelta(seconds=0) or abs((r.descentes[0].debut - (DEBUT + timedelta(minutes=6))).total_seconds()) <= 15, "début de la première descente : à la fin de la première remontée")
verifie(len(r.descentes[0].points) == 3 and r.descentes[0].points[0][0] > 45.9, "chaque descente garde trois points seulement (début, milieu, fin)")
bruitee = activites.analyser(activites.lire_gpx(gpx(trace(bruit=4.0))))
verifie(len(bruitee.descentes) == 4, "avec 4 m de bruit d'altitude (GPS) : toujours 4 descentes, pas de faux positifs")
verifie(len(activites.analyser(activites.lire_gpx(gpx(trace(descentes=1, remontee_m=40)))).descentes) == 0, "une pente de 40 m seulement : pas une descente")
verifie(activites.analyser(points[:5]) is None, "trace de moins de 10 points : rien")
lignes = trace(descentes=2)
lignes[100] = (lignes[100][0], 47.5, 7.5, lignes[100][3], lignes[100][4])  # saut de GPS de plus de 100 km
verifie(activites.analyser(activites.lire_gpx(gpx(lignes))).vitesse_max_kmh < 80, "un saut de GPS n'invente pas une vitesse absurde")
creux = trace(descentes=1)
creux = creux[:36] + [(t, la, lo, al - 8, v) for t, la, lo, al, v in creux[36:60]] + creux[60:]  # un creux de 8 m pendant la remontée
verifie(len(activites.analyser(activites.lire_gpx(gpx(creux))).descentes) == 1, "un creux de 8 m pendant la remontée n'ajoute pas de descente")
if FIT:
    rf = activites.analyser(activites.lire_fit(fit(trace())))
    verifie(len(rf.descentes) == 4 and 52 <= rf.vitesse_max_kmh <= 56, "FIT : mêmes descentes et vitesse que le GPX, vitesse lue sur l'appareil")

# --- Enregistrement ------------------------------------------------------------------------------------------------
horloge.regler(horloge.vers_utc(datetime(2026, 12, 13, 20, 0)))
with SessionLocal() as s:
    maxime, adr = (s.scalar(select(Personne).where(Personne.prenom == p)) for p in ("Maxime", "Adrien"))
    verifie("n'arrive pas à lire" in activites.enregistrer(s, maxime, "x.gpx", b"pas du xml"), "fichier illisible : message clair")
    verifie("n'arrive pas à lire" in activites.enregistrer(s, maxime, "x.fit", b"pas un fit"), "FIT illisible : message clair")
    verifie("aucune descente" in activites.enregistrer(s, maxime, "court.gpx", gpx(trace(descentes=1)[:20])).lower() and s.scalar(select(Activite.id)) is None, "trace sans descente : rien n'est gardé")
    contenu = gpx(trace())
    rep = activites.enregistrer(s, maxime, "samedi.gpx", contenu)
    verifie("4 descentes, 1 6" in rep and re.search(r"de 09:0[56] à 09:3[56]\.", rep) and "jamais utilisé pour te comparer" in rep, "résumé : descentes, heures de Paris, rappel que ce n'est pas comparé")
    j = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    verifie(j is not None and s.scalar(select(Activite)).journee_id == j.id, "rangé dans la journée du fichier (12 décembre), même envoyé le 13")
    verifie("déjà été envoyé" in activites.enregistrer(s, maxime, "samedi.gpx", contenu), "le même fichier envoyé deux fois n'est pas compté deux fois")
    verifie("4 descentes" in activites.enregistrer(s, adr, "adrien.gpx", contenu), "le même fichier chez une autre personne est le sien")
    a = s.scalar(select(Activite).where(Activite.personne_id == maxime.id))
    segments = json.loads(a.segments)
    verifie(len(segments) == 4 and all(len(x["points"]) == 3 for x in segments) and "alt" not in a.segments, "confidentialité : 3 points par descente gardés, jamais la trace complète")
    verifie(a.nb_descentes == 4 and 1570 <= a.denivele_neg <= 1610, "totaux gardés en base")
    t = stats.stats_personne(s, maxime)
    verifie("fichiers d'activité (1) : 4 descentes mesurées, 1 6" in t and "jamais comparé" in t, "/stats : les totaux de ses propres fichiers")
    verifie("fichiers d'activité" not in stats.stats_tous(s) and "descentes mesurées" not in stats.stats_tous(s), "/stats tous : jamais de comparaison sur les fichiers d'activité")
    verifie("m de dénivelé négatif cumulés" in "\n".join(briefing.faits_recap(s, s.scalar(select(Weekend)))) and "dans 2 fichiers" in "\n".join(briefing.faits_recap(s, s.scalar(select(Weekend)))),
            "récap : seulement un total du groupe, pas de détail par personne")
    verifie("Maxime" not in [f for f in briefing.faits_recap(s, s.scalar(select(Weekend))) if "dénivelé" in f][0], "récap : aucune personne nommée dans la ligne du dénivelé")

# --- Couche Telegram -------------------------------------------------------------------------------------------------
async def telegram():
    def maj(taille=1000, nom="activite.gpx", octets=b""):
        doc = SimpleNamespace(file_size=taille, file_name=nom, get_file=AsyncMock(return_value=SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(octets)))))
        return SimpleNamespace(message=SimpleNamespace(document=doc, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    up = maj(taille=30 * 1024 * 1024)
    await commandes_stats.fichier_activite(up, SimpleNamespace())
    verifie("Trop lourd" in up.message.reply_text.call_args[0][0], "fichier de plus de 20 Mo refusé")
    up = maj(octets=gpx(trace(descentes=2)), nom="dimanche.gpx")
    await commandes_stats.fichier_activite(up, SimpleNamespace())
    verifie("2 descentes" in up.message.reply_text.call_args[0][0], "fichier reçu par Telegram : analysé et résumé")

asyncio.run(telegram())
print("\nTout passe.")
