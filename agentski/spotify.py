"""Playlist unique du week-end annuel sur le compte Spotify de Maxime : /son [titre] ou un lien Spotify collé dans le groupe.

Appels vérifiés sur la documentation officielle : POST /me/playlists, POST /playlists/{id}/items, GET /search (10 résultats
au plus), renouvellement du jeton par POST accounts.spotify.com/api/token. L'aperçu audio n'existe plus (preview_url est abandonné).
Lecture : PUT /me/player/play et /me/player/pause, GET /me/player/devices (Premium, appareil Spotify actif).
Mode développeur : le propriétaire de l'application doit avoir Spotify Premium, 5 utilisateurs au plus."""
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, definir_reglage, journee_du_jour, reglage
from .models import ChampJournal, Journee, Morceau, Personne, Reglage, Weekend
from .rappels import NOMS_MOIS

log = logging.getLogger("agentski")
API = "https://api.spotify.com/v1"
LIEN = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)([A-Za-z0-9]{22})")
_jeton: dict = {"valeur": None, "expire": 0.0}


def _panne(e: Exception, debut: str = "Spotify ne répond pas pour l'instant") -> str:
    """Message d'une panne qui n'est pas une réponse d'erreur de Spotify : on garde la cause dans les journaux et on en donne le type, au lieu de la cacher."""
    log.warning("Spotify : %r", e, exc_info=True)
    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
        return f"{debut} (délai dépassé : l'appareil ne répond pas, appli Spotify en veille ?). Réessaie avec l'appli ouverte au premier plan."
    return f"{debut} ({type(e).__name__}). Réessaie dans un moment."


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
    except Exception as e:
        return _panne(e), None
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


def playlist_annuelle(session: Session, http=_http) -> str:
    """L'unique playlist, créée au premier ajout : celle qu'on écoutera pendant le week-end annuel."""
    existante = reglage(session, "playlist_annuelle")
    if existante:
        return existante
    r = _api("POST", "/me/playlists", {"name": config.PLAYLIST_NOM, "public": False, "description": "La bande-son du week-end annuel : tout le monde y ajoute ses morceaux"}, http)
    definir_reglage(session, "playlist_annuelle", r["id"])
    return r["id"]


def reinitialiser(session: Session) -> str:
    """Le bot oublie la playlist et ses morceaux : le prochain /son crée une nouvelle playlist. Rien n'est modifié sur Spotify."""
    nb = session.scalar(select(func.count()).select_from(Morceau)) or 0
    ligne = session.get(Reglage, "playlist_annuelle")
    if ligne is not None:
        session.delete(ligne)
    session.execute(delete(Morceau))
    session.commit()
    return (f"🧹 Playlist réinitialisée : {nb} morceau{'x' if nb > 1 else ''} oublié{'s' if nb > 1 else ''}. "
            f"Le prochain /son créera une nouvelle playlist « {config.PLAYLIST_NOM} » sur ton Spotify (l'ancienne n'est pas touchée).")


def ajouter_morceau(session: Session, auteur: Personne, texte: str, http=_http) -> str:
    """Ajoute un morceau (lien Spotify) à l'unique playlist du week-end annuel. Possible toute l'année, sans journée de ski."""
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
        if session.scalar(select(Morceau.id).where(Morceau.track_id == piste["id"])):
            cle = f"monotitre:{auteur.id}"  # compteur pour le trophée Mono-titre
            definir_reglage(session, cle, str(int(reglage(session, cle) or 0) + 1))
            return f"🎵 « {piste['name']} » est déjà dans la playlist du week-end annuel."
        playlist = playlist_annuelle(session, http)
        _api("POST", f"/playlists/{playlist}/items", {"uris": [piste["uri"]]}, http)
    except SpotifyAbsent:
        return "Spotify n'est pas configuré."
    except Exception as e:
        return _panne(e, "Spotify ne répond pas pour l'instant : le morceau n'est pas ajouté")
    artistes = ", ".join(a["name"] for a in piste.get("artists", []))
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui()))  # rattaché à la journée du jour seulement si elle existe
    session.add(Morceau(weekend_id=journee.weekend_id if journee else None, journee_id=journee.id if journee else None, personne_id=auteur.id,
                        track_id=piste["id"], titre=piste["name"], artistes=artistes, cree_a=_maintenant_aware()))
    if journee:
        session.add(ChampJournal(journee_id=journee.id, cle="morceau", valeur=f"{piste['name']} — {artistes}", phrase_source="Spotify",
                                 detail=auteur.prenom, verifie=True))
    session.commit()
    return f"🎵 Ajouté à la playlist du week-end annuel : {piste['name']} — {artistes}"


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


DELAI_VERIF = 1.0   # secondes entre deux vérifications de ce qui joue
ESSAIS_VERIF = 6    # un téléphone met parfois plusieurs secondes à démarrer : on laisse jusqu'à ESSAIS_VERIF * DELAI_VERIF


def _joue_bien(track_id: str, http, attendre) -> tuple[bool, str]:
    """Le morceau demandé est-il vraiment en cours de lecture ? Spotify répond 204 dès qu'il a reçu l'ordre, même si l'appareil n'obéit pas.
    Renvoie aussi ce que Spotify rapporte, pour que le message d'échec dise ce qui se passe réellement."""
    constat = "rien n'est chargé sur l'appareil"
    for _ in range(ESSAIS_VERIF):
        attendre(DELAI_VERIF)
        try:
            etat = _api("GET", "/me/player", http=http)
        except Exception:
            return True, ""  # impossible de vérifier : on ne prétend pas le contraire
        item = (etat or {}).get("item") or {}
        if item and track_id in (item.get("id"), (item.get("linked_from") or {}).get("id")):  # un titre « relié » a un autre identifiant selon le pays
            if etat.get("is_playing") is True:
                return True, ""
            constat = "le morceau est chargé mais en pause"
        elif item:
            constat = f"l'appareil joue autre chose (« {item.get('name', '?')} »)"
        elif etat:
            constat = "l'appareil est connecté mais à l'arrêt, sans morceau chargé"
    return False, constat


def lancer_musique(auteur_texte: str, http=_http, position_ms: int | None = None, attendre=time.sleep) -> str:
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
            # Sur iPhone, un ordre « uris » est accepté (204) mais ignoré : l'appli perd sa session sans rien lancer. Avec l'album en contexte
            # et le morceau en point de départ (offset), l'iPhone joue. La suite de l'album suit ensuite le morceau choisi.
            album = (piste.get("album") or {}).get("uri")
            corps = {"context_uri": album, "offset": {"uri": piste["uri"]}} if album else {"uris": [piste["uri"]]}
            if position_ms:
                corps["position_ms"] = int(position_ms)
            titre = f" : {piste['name']} — {', '.join(a['name'] for a in piste.get('artists', []))}"
        appareils = _api("GET", "/me/player/devices", http=http).get("devices", [])
        appareil, explication = _choisir_appareil(appareils)
        if appareil is None:
            return explication
        chemin = "/me/player/play" + ("" if appareil.get("is_active") else f"?device_id={urllib.parse.quote(appareil['id'])}")
        _api("PUT", chemin, corps, http)
        if corps is not None and not (ok := _joue_bien(piste["id"], http, attendre))[0]:
            # l'appareil a arrêté l'ancien morceau sans démarrer le nouveau (appli en veille, souvent sur téléphone) :
            # on lui rend la main d'abord (transfert avec lecture), puis on relance le morceau sur cet appareil
            _api("PUT", "/me/player", {"device_ids": [appareil["id"]], "play": True}, http)
            attendre(DELAI_VERIF)
            _api("PUT", f"/me/player/play?device_id={urllib.parse.quote(appareil['id'])}", corps, http)
            ok = _joue_bien(piste["id"], http, attendre)
            if not ok[0]:
                return (f"⚠️ Spotify a reçu l'ordre mais « {appareil['name']} » ne lance pas le morceau (Spotify indique : {ok[1]}). "
                        "Laisse l'appli Spotify ouverte au premier plan, écran allumé, avec un morceau en cours, puis refais /play.")
    except SpotifyAbsent:
        return "Spotify n'est pas configuré."
    except SpotifyErreur as e:
        return _erreur_lecture(e)
    except Exception as e:
        return _panne(e)
    return f"▶️ Lecture sur {appareil['name']}{titre}"


def mettre_en_pause(http=_http) -> str:
    if not configure():
        return "Spotify n'est pas branché : il faut créer une application et lancer scripts/spotify_autoriser.py."
    try:
        _api("PUT", "/me/player/pause", http=http)
    except SpotifyErreur as e:
        if e.code == 404 or (e.code == 403 and "Restriction violated" in str(e)):  # rien ne joue, ou déjà en pause : ce n'est pas un problème d'autorisation
            return "Rien n'est en cours de lecture sur un appareil Spotify actif."
        return _erreur_lecture(e)
    except Exception as e:
        return _panne(e)
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
    except Exception as e:
        return _panne(e)
    return f"➕ Ajouté à la file d'attente sur {appareil['name']} : {_detail(piste)}"
