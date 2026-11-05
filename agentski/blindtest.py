"""Blind test sur la playlist du groupe : Krok lance un extrait sur l'appareil Spotify actif, le groupe devine à l'écrit.
Le titre vaut 2 points, l'artiste 1. Cœur indépendant de Telegram : l'état de la partie est un réglage JSON par conversation.

Les morceaux viennent de ceux que le groupe a ajoutés avec /son. Le son sort de l'appareil Spotify de l'organisateur."""
import difflib
import json
import random
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import spotify
from .amis import definir_reglage, nom_krok, reglage
from .models import Morceau, Personne, Reglage

MANCHES_PAR_DEFAUT = 5
MANCHES_MAX = 10
MIN_MORCEAUX = 3
SECONDES_ECOUTE = 25   # l'extrait joue puis la musique est mise en pause
SECONDES_MANCHE = 75   # sans réponse complète, Krok révèle au bout de ce délai
POINTS_TITRE, POINTS_ARTISTE = 2, 1
SEUIL_PROCHE = 0.85    # ressemblance minimale pour pardonner une faute de frappe


def _cle(chat_id: int) -> str:
    return f"blindtest:{chat_id}"


def _etat(session: Session, chat_id: int) -> dict | None:
    brut = reglage(session, _cle(chat_id))
    return json.loads(brut) if brut else None


def _ecrire(session: Session, chat_id: int, etat: dict) -> None:
    definir_reglage(session, _cle(chat_id), json.dumps(etat, ensure_ascii=False))


def en_cours(session: Session, chat_id: int) -> bool:
    return _etat(session, chat_id) is not None


def manche_ouverte(session: Session, chat_id: int) -> str | None:
    """Le jeton de la manche en cours si l'on attend encore des réponses, sinon None."""
    etat = _etat(session, chat_id)
    return etat["courante"]["jeton"] if etat and etat.get("courante") else None


def arreter(session: Session, chat_id: int) -> str:
    ligne = session.get(Reglage, _cle(chat_id))
    if ligne is None:
        return "Aucun blind test en cours."
    session.delete(ligne)
    session.commit()
    return "🛑 Blind test arrêté."


# --- Comparaison des réponses ---------------------------------------------------------------------------------

def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texte).split())


def titre_principal(titre: str) -> str:
    """« Get Lucky (feat. X) - Remastered » devient « Get Lucky » : ce qu'on peut raisonnablement demander de deviner."""
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", titre).split(" - ")[0].strip()


def _contient(proposition: str, cherche: str) -> bool:
    """Le texte cherché apparaît comme mots entiers dans la proposition, ou lui ressemble presque tout entier."""
    if not cherche:
        return False
    if f" {cherche} " in f" {proposition} ":
        return True
    return difflib.SequenceMatcher(None, proposition, cherche).ratio() >= SEUIL_PROCHE


def verifier(proposition: str, titre: str, artistes: str) -> tuple[bool, bool]:
    """(titre trouvé, artiste trouvé) pour une phrase écrite par un joueur."""
    p = normaliser(proposition)
    titre_ok = _contient(p, normaliser(titre_principal(titre)))
    artiste_ok = any(_contient(p, normaliser(a)) for a in artistes.split(","))
    return titre_ok, artiste_ok


# --- Déroulement ----------------------------------------------------------------------------------------------

def morceaux_disponibles(session: Session) -> list[Morceau]:
    vus, sortie = set(), []
    for m in session.scalars(select(Morceau).order_by(Morceau.id)):
        if m.track_id not in vus:
            vus.add(m.track_id)
            sortie.append(m)
    return sortie


def demarrer(session: Session, chat_id: int, manches: int | None = None) -> str | None:
    """Prépare la partie. Renvoie un message d'erreur, ou None si elle est prête (la première manche reste à lancer)."""
    if en_cours(session, chat_id):
        return "Un blind test est déjà en cours. /blindtest stop pour l'arrêter."
    dispo = len(morceaux_disponibles(session))
    if dispo < MIN_MORCEAUX:
        return f"Il me faut au moins {MIN_MORCEAUX} morceaux dans la playlist du week-end annuel (/son titre) : il y en a {dispo}."
    manches = max(1, min(manches or MANCHES_PAR_DEFAUT, MANCHES_MAX, dispo))
    _ecrire(session, chat_id, {"manches": manches, "numero": 0, "deja": [], "scores": {}, "courante": None})
    return None


def total_manches(session: Session, chat_id: int) -> int:
    return _etat(session, chat_id)["manches"]


@dataclass
class Manche:
    numero: int
    total: int
    jeton: str


def lancer_manche(session: Session, chat_id: int, http=spotify._http, hasard=random) -> Manche | str:
    """Choisit un morceau pas encore joué et le lance à un endroit au hasard. Renvoie la manche, ou le message d'erreur de Spotify."""
    etat = _etat(session, chat_id)
    restants = [m for m in morceaux_disponibles(session) if m.track_id not in etat["deja"]]
    if not restants:
        return "Plus de morceau à faire deviner."
    choisi = hasard.choice(restants)
    try:
        piste = spotify.lire_morceau(choisi.track_id, http)
    except Exception:
        piste = None
    duree = (piste or {}).get("duration_ms") or 0
    position = int(duree * hasard.uniform(0.15, 0.55)) if duree else None
    reponse = spotify.lancer_musique(f"spotify:track:{choisi.track_id}", http, position)
    if not reponse.startswith("▶️"):
        if etat["numero"] == 0:
            arreter(session, chat_id)  # la partie n'a pas commencé : on repart de zéro à la prochaine tentative
        return reponse
    etat["numero"] += 1
    etat["deja"].append(choisi.track_id)
    jeton = f"{hasard.getrandbits(32):08x}"
    etat["courante"] = {"jeton": jeton, "titre": choisi.titre, "artistes": choisi.artistes, "titre_trouve": False, "artiste_trouve": False,
                        "proposeur": choisi.personne_id}
    _ecrire(session, chat_id, etat)
    return Manche(etat["numero"], etat["manches"], jeton)


@dataclass
class Resultat:
    texte: str
    termine: bool  # titre et artiste trouvés : la manche peut être révélée tout de suite


def proposer(session: Session, chat_id: int, joueur: Personne, texte: str) -> Resultat | None:
    """Une phrase écrite par un joueur. Renvoie None si elle ne trouve rien de nouveau (Krok ne dit rien)."""
    etat = _etat(session, chat_id)
    if not etat or not etat.get("courante"):
        return None
    c = etat["courante"]
    titre_ok, artiste_ok = verifier(texte, c["titre"], c["artistes"])
    nom = nom_krok(joueur)
    gains, lignes = 0, []
    if titre_ok and not c["titre_trouve"]:
        c["titre_trouve"], gains = True, gains + POINTS_TITRE
        lignes.append(f"le titre ({POINTS_TITRE} pts)")
    if artiste_ok and not c["artiste_trouve"]:
        c["artiste_trouve"], gains = True, gains + POINTS_ARTISTE
        lignes.append(f"l'artiste ({POINTS_ARTISTE} pt)")
    if not gains:
        return None
    etat["scores"][nom] = etat["scores"].get(nom, 0) + gains
    _ecrire(session, chat_id, etat)
    return Resultat(f"✅ {nom} trouve {' et '.join(lignes)} !", c["titre_trouve"] and c["artiste_trouve"])


def _classement(scores: dict[str, int]) -> str:
    medailles = ["🥇", "🥈", "🥉"]
    ordre = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return "\n".join(f"{medailles[i] if i < 3 else '▫️'} {nom} : {pts} pt{'s' if pts > 1 else ''}" for i, (nom, pts) in enumerate(ordre)) or "Personne n'a marqué de point."


def reveler(session: Session, chat_id: int) -> tuple[str, bool] | None:
    """Ferme la manche et montre la réponse. Renvoie (texte, partie_finie), ou None s'il n'y a rien à révéler."""
    etat = _etat(session, chat_id)
    if not etat or not etat.get("courante"):
        return None
    c = etat["courante"]
    proposeur = session.get(Personne, c["proposeur"])
    texte = f"🎵 C'était « {c['titre']} » de {c['artistes']}" + (f", ajouté par {nom_krok(proposeur)}." if proposeur else ".")
    if not (c["titre_trouve"] or c["artiste_trouve"]):
        texte += " Personne n'a trouvé 😅"
    etat["courante"] = None
    if etat["numero"] >= etat["manches"]:
        texte += f"\n\n🏁 Fin du blind test !\n{_classement(etat['scores'])}"
        session.delete(session.get(Reglage, _cle(chat_id)))
        session.commit()
        return texte, True
    texte += f"\n\nScores :\n{_classement(etat['scores'])}"
    _ecrire(session, chat_id, etat)
    return texte, False
