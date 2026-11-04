"""Playlist du week-end sur le compte Spotify de Maxime : /son [titre] ou un lien Spotify collé dans le groupe.

Appels vérifiés sur la documentation officielle : POST /me/playlists, POST /playlists/{id}/items, GET /search (10 résultats
au plus), renouvellement du jeton par POST accounts.spotify.com/api/token. L'aperçu audio n'existe plus (preview_url est abandonné).
Lecture : PUT /me/player/play et /me/player/pause, GET /me/player/devices (Premium, appareil Spotify actif).
Mode développeur : le propriétaire de l'application doit avoir Spotify Premium, 5 utilisateurs au plus."""
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, definir_reglage, journee_du_jour, reglage
from .models import ChampJournal, Journee, Morceau, Personne, Weekend
from .rappels import NOMS_MOIS

API = "https://api.spotify.com/v1"
LIEN = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)([A-Za-z0-9]{22})")
_jeton: dict = {"valeur": None, "expire": 0.0}


class SpotifyAbsent(Exception):
    """Spotify n'est pas configuré."""


class SpotifyErreur(RuntimeError):
    """Réponse d'erreur de Spotify, avec son code HTTP."""

    def __init__(self, code: int, corps: str):
        super().__init__(f"Spotify {code} : {corps}")
        self.code = code


def configure() -> bool:
    return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET and config.SPOTIFY_REFRESH_TOKEN)


def extraire_id(texte: str) -> str | None:
    trouve = LIEN.search(texte)
    return trouve.group(1) if trouve else None


def _http(methode: str, url: str, entetes: dict, corps: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=corps, headers=entetes, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            brut = r.read()
            return json.loads(brut) if brut else {}
    except urllib.error.HTTPError as e:
        raise SpotifyErreur(e.code, e.read().decode()[:200]) from None


def jeton_acces(http=_http) -> str:
    """Jeton d'accès, renouvelé avec le jeton de rafraîchissement (valable 6 mois, à renouveler avec le script d'autorisation)."""
    if not configure():
        raise SpotifyAbsent()
    if _jeton["valeur"] and time.time() < _jeton["expire"] - 60:
        return _jeton["valeur"]
    identifiants = base64.b64encode(f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()).decode()
    r = http("POST", "https://accounts.spotify.com/api/token",
             {"Authorization": f"Basic {identifiants}", "Content-Type": "application/x-www-form-urlencoded"},
             urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": config.SPOTIFY_REFRESH_TOKEN}).encode())
    _jeton["valeur"], _jeton["expire"] = r["access_token"], time.time() + r.get("expires_in", 3600)
    if r.get("refresh_token"):  # Spotify peut en renvoyer un nouveau : il remplace l'ancien
        config.SPOTIFY_REFRESH_TOKEN = r["refresh_token"]
    return _jeton["valeur"]


def _api(methode: str, chemin: str, corps: dict | None = None, http=_http) -> dict:
    donnees = json.dumps(corps).encode() if corps is not None else (b"" if methode != "GET" else None)  # Spotify veut un Content-Length, même vide
    return http(methode, f"{API}{chemin}", {"Authorization": f"Bearer {jeton_acces(http)}", "Content-Type": "application/json"}, donnees)


def chercher_titres(recherche: str, n: int = 3, http=_http) -> list[dict]:
    """Les n premiers résultats d'une recherche de titre (Spotify en renvoie 10 au plus)."""
    r = _api("GET", "/search?" + urllib.parse.urlencode({"q": recherche, "type": "track", "limit": n}), http=http)
    return ((r.get("tracks") or {}).get("items") or [])[:n]


def _detail(piste: dict) -> str:
    album = piste.get("album") or {}
    annee = (album.get("release_date") or "")[:4]
    complement = ", ".join(x for x in (album.get("name"), annee) if x)
    return f"{piste['name']} — {', '.join(a['name'] for a in piste.get('artists', []))}" + (f" ({complement})" if complement else "")


def proposer(texte: str, action: str, telegram_id: int, http=_http) -> tuple[str, list | None]:
    """Les trois premiers résultats d'un titre, à choisir en boutons : on ne prend jamais le premier au hasard.
    `action` : « son » (ajouter à la playlist) ou « play » (lancer). Le bouton porte celui qui a lancé la recherche."""
    if not configure():
        return "Spotify n'est pas branché : il faut créer une application et lancer scripts/spotify_autoriser.py.", None
    texte = texte.strip()
    if not texte:
        return "Format : un titre à chercher, ou un lien Spotify.", None
    try:
        pistes = chercher_titres(texte, 3, http)
    except SpotifyErreur as e:
        return _erreur_lecture(e), None
    except Exception:
        return "Spotify ne répond pas pour l'instant : réessaie dans un moment.", None
    if not pistes:
        return f"Je ne trouve pas « {texte} » sur Spotify.", None
    lignes = "\n".join(f"{i}. {_detail(p)}" for i, p in enumerate(pistes, 1))
    boutons = [[(str(i), f"s|{action}|{p['id']}|{telegram_id}") for i, p in enumerate(pistes, 1)] + [("Aucun", f"s|x|0|{telegram_id}")]]
    return f"🔎 Voici les {len(pistes)} premiers résultats pour « {texte} ». Lequel ?\n{lignes}", boutons


def lire_morceau(track_id: str, http=_http) -> dict | None:
    """Le morceau, ou None s'il n'existe pas. Toute autre erreur (panne, limite) remonte au lieu de passer pour « introuvable »."""
    try:
        return _api("GET", f"/tracks/{track_id}", http=http)
    except SpotifyErreur as e:
        if e.code in (400, 404):
            return None
        raise


def playlist_du_weekend(session: Session, weekend: Weekend, http=_http) -> str:
    cle = f"playlist:{weekend.id}"
    existante = reglage(session, cle)
    if existante:
        return existante
    d1, d2 = weekend.date_debut, weekend.date_fin
    nom = f"Chronique ski — {d1.day} au {d2.day} {NOMS_MOIS[d2.month - 1]} {d2.year}" if d1 != d2 else f"Chronique ski — {d1.day} {NOMS_MOIS[d1.month - 1]} {d1.year}"
    r = _api("POST", "/me/playlists", {"name": nom, "public": False, "description": "La bande-son de nos week-ends de ski"}, http)
    definir_reglage(session, cle, r["id"])
    return r["id"]


def ajouter_morceau(session: Session, auteur: Personne, texte: str, http=_http) -> str:
    """Ajoute un morceau (lien ou titre) à la playlist du week-end et le note au journal."""
    if not configure():
        return ("Spotify n'est pas branché : il faut créer une application sur developer.spotify.com (compte Premium requis) "
                "puis lancer scripts/spotify_autoriser.py. En attendant, /son ne peut rien ajouter.")
    texte = texte.strip()
    if not texte:
        return "Format : /son Daft Punk Get Lucky, ou colle un lien Spotify."
    try:
        track_id = extraire_id(texte)
        piste = lire_morceau(track_id, http) if track_id else None  # un titre écrit passe par proposer() : jamais le premier résultat au hasard
        if piste is None:
            return f"Je ne trouve pas « {texte} » sur Spotify." if track_id else "Il me faut un lien Spotify : pour chercher un titre, utilise /son titre."
        journee = journee_du_jour(session, auteur)
        weekend = session.get(Weekend, journee.weekend_id)
        if session.scalar(select(Morceau.id).where(Morceau.weekend_id == weekend.id, Morceau.track_id == piste["id"])):
            return f"🎵 « {piste['name']} » est déjà dans la playlist de ce week-end."
        playlist = playlist_du_weekend(session, weekend, http)
        _api("POST", f"/playlists/{playlist}/items", {"uris": [piste["uri"]]}, http)
    except SpotifyAbsent:
        return "Spotify n'est pas configuré."
    except Exception:
        return "Spotify ne répond pas pour l'instant : le morceau n'est pas ajouté, réessaie dans un moment."
    artistes = ", ".join(a["name"] for a in piste.get("artists", []))
    session.add(Morceau(weekend_id=weekend.id, journee_id=journee.id, personne_id=auteur.id, track_id=piste["id"], titre=piste["name"],
                        artistes=artistes, cree_a=_maintenant_aware()))
    session.add(ChampJournal(journee_id=journee.id, cle="morceau", valeur=f"{piste['name']} — {artistes}", phrase_source="Spotify",
                             detail=auteur.prenom, verifie=True))
    session.commit()
    return f"🎵 Ajouté à la playlist : {piste['name']} — {artistes}"


# --- Lecture sur un appareil Spotify -------------------------------------------------------------------------------

def _erreur_lecture(e: SpotifyErreur) -> str:
    if e.code == 403:
        return ("Spotify refuse : soit il manque l'autorisation de lecture (relance scripts/spotify_autoriser.py), "
                "soit le compte n'est pas Premium, qui est obligatoire pour commander la lecture.")
    if e.code == 429:
        return "Spotify demande d'attendre un peu : réessaie dans une minute."
    if e.code == 404:
        return "Spotify ne trouve aucun appareil en lecture : lance d'abord un morceau (/play), puis réessaie."
    return "Spotify ne répond pas comme prévu pour l'instant : réessaie dans un moment."


def _choisir_appareil(appareils: list[dict]) -> tuple[dict | None, str]:
    """L'appareil actif, ou le seul disponible ; sinon None et le message qui explique quoi faire."""
    libres = [a for a in appareils if not a.get("is_restricted")]
    actif = next((a for a in libres if a.get("is_active")), None)
    if actif:
        return actif, ""
    if len(libres) == 1:
        return libres[0], ""
    if not libres:
        return None, ("Aucun appareil Spotify trouvé : ouvre Spotify sur ton téléphone ou ton Mac, lance un morceau une seconde "
                      "pour qu'il devienne l'appareil actif, puis réessaie.")
    noms = ", ".join(a["name"] for a in libres)
    return None, f"Plusieurs appareils Spotify ({noms}) mais aucun n'est actif : lance un morceau sur celui que tu veux, puis réessaie."


def lancer_musique(auteur_texte: str, http=_http) -> str:
    """/play [titre ou lien] : lance le morceau sur l'appareil Spotify actif ; sans argument, reprend la lecture.
    Le son sort de l'appareil où Spotify est ouvert, jamais de Telegram ni du serveur."""
    if not configure():
        return "Spotify n'est pas branché : il faut créer une application et lancer scripts/spotify_autoriser.py."
    texte = auteur_texte.strip()
    try:
        corps, titre = None, ""
        if texte:
            track_id = extraire_id(texte)
            piste = lire_morceau(track_id, http) if track_id else None
            if piste is None:
                return f"Je ne trouve pas « {texte} » sur Spotify." if track_id else "Il me faut un lien Spotify : pour chercher un titre, utilise /play titre."
            corps = {"uris": [piste["uri"]]}
            titre = f" : {piste['name']} — {', '.join(a['name'] for a in piste.get('artists', []))}"
        appareils = _api("GET", "/me/player/devices", http=http).get("devices", [])
        appareil, explication = _choisir_appareil(appareils)
        if appareil is None:
            return explication
        chemin = "/me/player/play" + ("" if appareil.get("is_active") else f"?device_id={urllib.parse.quote(appareil['id'])}")
        _api("PUT", chemin, corps, http)
    except SpotifyAbsent:
        return "Spotify n'est pas configuré."
    except SpotifyErreur as e:
        return _erreur_lecture(e)
    except Exception:
        return "Spotify ne répond pas pour l'instant : réessaie dans un moment."
    return f"▶️ Lecture sur {appareil['name']}{titre}"


def mettre_en_pause(http=_http) -> str:
    if not configure():
        return "Spotify n'est pas branché : il faut créer une application et lancer scripts/spotify_autoriser.py."
    try:
        _api("PUT", "/me/player/pause", http=http)
    except SpotifyErreur as e:
        return _erreur_lecture(e) if e.code != 404 else "Rien n'est en cours de lecture sur un appareil Spotify actif."
    except Exception:
        return "Spotify ne répond pas pour l'instant : réessaie dans un moment."
    return "⏸ Musique en pause."


def mettre_en_file(texte: str, http=_http) -> str:
    """/file [titre ou lien] : ajoute le morceau à la file d'attente de l'appareil Spotify, sans couper ce qui joue.
    POST /me/player/queue (Premium) ; un titre écrit passe d'abord par proposer()."""
    if not configure():
        return "Spotify n'est pas branché : il faut créer une application et lancer scripts/spotify_autoriser.py."
    track_id = extraire_id(texte)
    if not track_id:
        return "Il me faut un lien Spotify : pour chercher un titre, utilise /file titre."
    try:
        piste = lire_morceau(track_id, http)
        if piste is None:
            return f"Je ne trouve pas « {texte.strip()} » sur Spotify."
        appareil, explication = _choisir_appareil(_api("GET", "/me/player/devices", http=http).get("devices", []))
        if appareil is None:
            return explication
        parametres = {"uri": piste["uri"]}
        if not appareil.get("is_active"):
            parametres["device_id"] = appareil["id"]
        _api("POST", "/me/player/queue?" + urllib.parse.urlencode(parametres), http=http)
    except SpotifyAbsent:
        return "Spotify n'est pas configuré."
    except SpotifyErreur as e:
        return _erreur_lecture(e)
    except Exception:
        return "Spotify ne répond pas pour l'instant : réessaie dans un moment."
    return f"➕ Ajouté à la file d'attente sur {appareil['name']} : {_detail(piste)}"
