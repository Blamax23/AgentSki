"""Playlist du week-end sur le compte Spotify de Maxime : /son [titre] ou un lien Spotify collé dans le groupe.

Appels vérifiés sur la documentation officielle : POST /me/playlists, POST /playlists/{id}/items, GET /search (10 résultats
au plus), renouvellement du jeton par POST accounts.spotify.com/api/token. L'aperçu audio n'existe plus (preview_url est abandonné).
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
        raise RuntimeError(f"Spotify {e.code} : {e.read().decode()[:200]}") from None


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
    return http(methode, f"{API}{chemin}", {"Authorization": f"Bearer {jeton_acces(http)}", "Content-Type": "application/json"},
                json.dumps(corps).encode() if corps is not None else None)


def chercher_titre(recherche: str, http=_http) -> dict | None:
    r = _api("GET", "/search?" + urllib.parse.urlencode({"q": recherche, "type": "track", "limit": 1}), http=http)
    items = (r.get("tracks") or {}).get("items") or []
    return items[0] if items else None


def lire_morceau(track_id: str, http=_http) -> dict | None:
    try:
        return _api("GET", f"/tracks/{track_id}", http=http)
    except RuntimeError:
        return None


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
        piste = lire_morceau(track_id, http) if track_id else chercher_titre(texte, http)
        if piste is None:
            return f"Je ne trouve pas « {texte} » sur Spotify."
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
