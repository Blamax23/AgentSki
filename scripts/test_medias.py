"""Teste les vocaux gardés, la liste de /medias et le téléchargement sur disque (faux client Telegram, dossier jetable).
Lancer : .venv/bin/python scripts/test_medias.py"""
import os
import random
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "medias.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import horloge, medias, telechargement  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, Media, Personne  # noqa: E402

Base.metadata.create_all(engine)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a_heure(j, h, m=0):
    horloge.regler(horloge.vers_utc(datetime(j.year, j.month, j.day, h, m)))


with SessionLocal() as s:
    seed_personnes(s); sync_allowed_users(s)
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    # --- vocaux hors entretien ---
    a_heure(date(2026, 12, 10), 20, 0)
    r = medias.ranger_vocal(s, adrien, "VOC1", 90_000, random.Random(1))
    verifie("Adrien" in r and "1 vocal de toi gardé" in r, "vocal : accusé chaleureux avec le compte de la personne")
    verifie("vocaux de toi gardés" in medias.ranger_vocal(s, adrien, "VOC2", 120_000), "vocal : le compte s'accorde au pluriel")
    verifie(s.scalar(select(Media).where(Media.file_id == "VOC1")).type == "voice" and medias.nombre_vocaux(s) == 2, "vocal : rangé comme média de type vocal")
    verifie(s.scalar(select(Media).where(Media.file_id == "VOC1")).journee_id is None, "vocal avant le départ : gardé sans journée de ski")

    # --- /medias ---
    a_heure(date(2027, 1, 1), 15, 30)
    s.add(Media(personne_id=adrien.id, type="video", file_id="GROS", taille=300 * 1024 * 1024, legende="La descente", cree_a=horloge.maintenant())); s.commit()
    a_heure(date(2027, 1, 2), 9, 5)
    for i in range(35):
        s.add(Media(personne_id=adrien.id, type="photo", file_id=f"P{i}", taille=80_000, cree_a=horloge.maintenant())); s.commit()
    liste, total, jour = medias.a_envoyer(s)
    verifie(jour == date(2027, 1, 2) and total == 35 and len(liste) == medias.PAR_PAGE, "/medias : par défaut le dernier jour qui a des médias, 30 par page")
    verifie(medias.a_envoyer(s, page=2)[0].__len__() == 5, "/medias : la deuxième page contient le reste")
    liste, total, jour = medias.a_envoyer(s, date(2027, 1, 1))
    verifie(total == 1 and liste[0][:2] == ("video", "GROS") and "Adrien · Vendredi 1 janvier 15:30" in liste[0][2] and "La descente" in liste[0][2],
            "/medias jour : le gros fichier est listé avec qui, quand et sa légende")
    verifie(medias.a_envoyer(s, tout=True)[1] == 38, "/medias tout : tous les médias, vocaux compris")
    verifie(medias.a_envoyer(s, date(2030, 1, 1))[1] == 0, "/medias : un jour sans média donne zéro")

    # --- téléchargement ---
    class Faux:
        def __init__(self): self.vus = []
        def chemin(self, file_id):
            self.vus.append(file_id)
            if file_id == "P3": raise RuntimeError("panne réseau")
            if file_id == "P4": raise telechargement.TropGros
            return f"photos/{file_id}.jpg"
        def contenu(self, chemin): return b"octets"
    dossier = Path(tempfile.mkdtemp())
    faux = Faux()
    bilan = telechargement.telecharger_tout(s, dossier, faux)
    verifie("GROS" not in faux.vus and len(bilan.gros) == 2, "téléchargement : les plus de 20 Mo ne sont même pas demandés à l'API, ils sont listés (le gros + P4 refusé)")
    verifie(any("300 Mo" in g for g in bilan.gros), "téléchargement : la liste des gros fichiers donne qui, quand et la taille")
    verifie(len(bilan.echecs) == 1 and "panne réseau" in bilan.echecs[0], "téléchargement : une panne ne bloque pas les autres fichiers")
    verifie(bilan.telecharges == 35, "téléchargement : tout le reste est téléchargé (33 photos + 2 vocaux)")
    fichiers = list(dossier.rglob("*.jpg"))
    verifie(any(f.parent.name == "Adrien" and f.parent.parent.name == "2027-01-02" for f in fichiers), "téléchargement : rangé par jour puis par prénom")
    verifie(not list(dossier.rglob("*.part")), "téléchargement : aucun fichier à moitié écrit")
    verifie(list(dossier.rglob("*.ogg")), "téléchargement : les vocaux sont enregistrés en .ogg")
    faux2 = Faux()
    bilan2 = telechargement.telecharger_tout(s, dossier, faux2)
    verifie(bilan2.deja_la == 35 and faux2.vus.count("P0") == 0, "téléchargement : relancé, il ne retélécharge pas ce qui est déjà là")
horloge.regler(None)
print("Tout est bon.")
