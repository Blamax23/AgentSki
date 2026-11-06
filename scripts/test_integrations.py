"""Teste l'agenda iCal, l'export .ics, la question à Alix et Spotify (faux serveur HTTP : aucun appel réseau).
Lancer : .venv/bin/python scripts/test_integrations.py"""
import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "integrations.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import agenda, amis, config, defis, horloge, integrations, rappels, spotify  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Base, ChampJournal, Defi, DefiVote, Journee, Morceau, Personne, Presence, Weekend  # noqa: E402

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)
    for prenom, tid in (("Adrien", 2), ("Alix", 4)):
        s.scalar(select(Personne).where(Personne.prenom == prenom)).telegram_id = tid
    s.commit()


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


def ics(*evenements):
    corps = "".join(f"BEGIN:VEVENT\nUID:{i}\nDTSTAMP:20260101T000000Z\nSUMMARY:{titre}\n{dates}\nEND:VEVENT\n" for i, (titre, dates) in enumerate(evenements))
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//FR\n{corps}END:VCALENDAR\n"


verifie(not config.SPOTIFY_CLIENT_ID and not config.AGENDA_ICAL_URL and config.OPENROUTER_API_KEY == "cle-de-test",
        "garde-fou : les tests n'ont accès à aucun vrai compte (Spotify, agenda, OpenRouter)")

# --- Agenda --------------------------------------------------------------------------------------------
jeudi = date(2026, 12, 10)
verifie(agenda.jours_ski(jeudi, ics(("Ski Flaine", "DTSTART;VALUE=DATE:20261212\nDTEND;VALUE=DATE:20261214"))) == [date(2026, 12, 12), date(2026, 12, 13)],
        "événement « Ski » sur deux journées entières : samedi et dimanche")
verifie(agenda.jours_ski(jeudi, ics(("SKI", "DTSTART:20261211T180000\nDTEND:20261211T220000"))) == [date(2026, 12, 11)], "événement horaire, casse ignorée : vendredi")
verifie(agenda.jours_ski(jeudi, ics(("Ski week-end", "DTSTART;VALUE=DATE:20261211\nDTEND;VALUE=DATE:20261215"))) == [date(2026, 12, 11), date(2026, 12, 12), date(2026, 12, 13)],
        "événement plus long que le week-end : limité de vendredi à dimanche")
verifie(agenda.jours_ski(jeudi, ics(("Dentiste", "DTSTART;VALUE=DATE:20261212\nDTEND;VALUE=DATE:20261213"))) == [], "un autre événement : rien")
verifie(agenda.jours_ski(jeudi, ics(("Skinny jeans", "DTSTART;VALUE=DATE:20261212\nDTEND;VALUE=DATE:20261213"))) == [], "« skinny » n'est pas « ski »")
verifie(agenda.jours_ski(jeudi, ics(("Ski", "DTSTART;VALUE=DATE:20261219\nDTEND;VALUE=DATE:20261220"))) == [], "un événement d'une autre semaine : rien")
verifie(agenda.jours_ski(jeudi, "n'importe quoi") == [] and agenda.jours_ski(jeudi, "") == [], "agenda illisible ou vide : rien, pas d'erreur")
verifie(agenda.masque_jours(jeudi, [date(2026, 12, 12), date(2026, 12, 13)]) == 0b110, "masque des jours cochés")
verifie(agenda.lire_ical("") is None, "sans lien configuré : aucune lecture")

with SessionLocal() as s:
    maxime, adr, alix = (s.scalar(select(Personne).where(Personne.prenom == p)) for p in ("Maxime", "Adrien", "Alix"))
    a(2026, 12, 10, 17, 0)
    rappels.planifier_jeudi(s)
    agenda.lire_ical = lambda *_: ics(("Ski", "DTSTART;VALUE=DATE:20261212\nDTEND;VALUE=DATE:20261214"))
    horloge.regler(horloge.vers_utc(datetime(2026, 12, 10, 18, 1)))
    envois = rappels.a_envoyer(s)
    msg = envois[0].message
    verifie("Je vois « Ski » dans ton agenda" in msg.texte and "✅ Samedi" in str(msg.boutons) and "✅ Dimanche" in str(msg.boutons) and "✅ Vendredi" not in str(msg.boutons),
            "la question du jeudi confirme : jours de l'agenda pré-cochés")
    agenda.lire_ical = lambda *_: None
    from agentski.models import Rappel
    r = s.scalar(select(Rappel).where(Rappel.type == "jeudi"))
    r.envoye_a, r.abandonne_a = None, None
    s.commit()
    a(2026, 12, 10, 18, 2)
    verifie("agenda" not in rappels.a_envoyer(s)[0].message.texte, "sans événement : la question habituelle")

    # --- Export .ics --------------------------------------------------------------------------------------
    verifie(agenda.exporter_ics(s) is None, "export sans journée de ski : rien")
    w = Weekend(date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 13), statut="confirme")
    s.add(w)
    s.flush()
    j = Journee(weekend_id=w.id, jour=date(2026, 12, 12))
    s.add(j)
    s.flush()
    s.add_all([Presence(journee_id=j.id, personne_id=maxime.id), Presence(journee_id=j.id, personne_id=adr.id)])
    s.commit()
    contenu = agenda.exporter_ics(s).decode()
    verifie("SUMMARY:Ski — Grand Massif" in contenu and "DTSTART;VALUE=DATE:20261212" in contenu and "Maxime\\, Adrien" in contenu, "export .ics : une journée de ski avec ses présents")

    # --- Alix -------------------------------------------------------------------------------------------------
    a(2026, 12, 7, 17, 0)  # lundi
    verifie(agenda.question_alix(s) == [], "lundi avant 18 h : rien")
    a(2026, 12, 8, 18, 5)  # mardi
    verifie(agenda.question_alix(s) == [], "un autre jour que le lundi : rien")
    a(2026, 12, 7, 18, 5)
    evs = agenda.question_alix(s)
    verifie(len(evs) == 1 and evs[0].destinataire == 4 and "Tu skies" in evs[0].texte and len(evs[0].boutons[0]) == 3, "lundi 18 h : question privée à Alix")
    verifie(agenda.question_alix(s) == [], "…une seule fois par semaine")
    verifie("2026-12-11" in evs[0].boutons[0][0][1], "les boutons portent le vendredi du week-end concerné")
    alix.telegram_id = None
    s.commit()
    a(2026, 12, 14, 18, 5)
    verifie(agenda.question_alix(s) == [], "Alix sans compte Telegram : rien")
    alix.telegram_id = 4
    s.commit()
    amis.definir_reglage(s, "alix:2026-12-11", "Les deux")
    suite = rappels.repondre(s, "j|2026-12-10|2|0|vj")  # samedi coché, on passe aux présents
    verifie("✅ Alix" in str(suite.boutons), "Alix a dit qu'elle skiait : pré-cochée dans « qui vient ? »")
    amis.definir_reglage(s, "alix:2026-12-11", "Pas de ski")
    verifie("✅ Alix" not in str(rappels.repondre(s, "j|2026-12-10|2|0|vj").boutons), "Alix ne skie pas : non cochée")

# --- Spotify (faux serveur) -------------------------------------------------------------------------------------
verifie(spotify.extraire_id("regarde https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc") == "4uLU6hMCjMI75M1A2tKUQC", "lien Spotify reconnu")
verifie(spotify.extraire_id("spotify:track:4uLU6hMCjMI75M1A2tKUQC") == "4uLU6hMCjMI75M1A2tKUQC" and spotify.extraire_id("rien") is None, "URI et texte sans lien")
with SessionLocal() as s:
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    verifie("pas branché" in spotify.ajouter_morceau(s, maxime, "Get Lucky"), "sans configuration : message clair")
    config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET, config.SPOTIFY_REFRESH_TOKEN = "cid", "sec", "refresh1"
    journal = []
    pistes = {"4uLU6hMCjMI75M1A2tKUQC": {"id": "4uLU6hMCjMI75M1A2tKUQC", "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "name": "Get Lucky",
                                         "artists": [{"name": "Daft Punk"}]},
              "5ChkMS8OtdzJeqyybCc9R5": {"id": "5ChkMS8OtdzJeqyybCc9R5", "uri": "spotify:track:5ChkMS8OtdzJeqyybCc9R5", "name": "Billie Jean",
                                         "artists": [{"name": "Michael Jackson"}]}}

    def http(methode, url, entetes, corps=None):
        journal.append((methode, url, entetes, corps))
        if "accounts.spotify.com" in url:
            return {"access_token": "AT", "expires_in": 3600, "refresh_token": "refresh2"}
        if url.startswith(spotify.API + "/search"):
            return {"tracks": {"items": [pistes["4uLU6hMCjMI75M1A2tKUQC"]]}}
        if "/tracks/" in url:
            return pistes.get(url.rsplit("/", 1)[1]) or (_ for _ in ()).throw(RuntimeError("404"))
        if url.endswith("/me/playlists"):
            return {"id": "PL1"}
        return {"snapshot_id": "x"}

    spotify._http = http
    a(2026, 12, 12, 15, 0)
    amis.journee_du_jour(s, maxime)  # une journée de ski existe : le morceau y sera aussi noté au journal
    reponse = spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http)
    verifie("Ajouté à la playlist du week-end annuel : Get Lucky — Daft Punk" in reponse, "/son avec un lien : morceau lu puis ajouté à la playlist du week-end annuel")
    jeton = journal[0]
    verifie(jeton[1] == "https://accounts.spotify.com/api/token" and jeton[2]["Authorization"].startswith("Basic ")
            and "grant_type=refresh_token" in jeton[3].decode() and "refresh_token=refresh1" in jeton[3].decode(), "jeton renouvelé avec le refresh token, en Basic")
    verifie(config.SPOTIFY_REFRESH_TOKEN == "refresh2", "un nouveau refresh token remplace l'ancien")
    lecture = next(c for c in journal if "/tracks/4uLU6hMCjMI75M1A2tKUQC" in c[1])
    verifie(lecture[2]["Authorization"] == "Bearer AT" and not any("/search" in c[1] for c in journal), "ajout par lien : lecture exacte du morceau, aucune recherche au hasard")
    creation = next(c for c in journal if c[1].endswith("/me/playlists"))
    verifie(json.loads(creation[3])["public"] is False and json.loads(creation[3])["name"] == config.PLAYLIST_NOM, "une seule playlist, demandée en privé, au nom configuré")
    ajout = next(c for c in journal if c[1].endswith("/playlists/PL1/items"))
    verifie(ajout[0] == "POST" and json.loads(ajout[3]) == {"uris": ["spotify:track:4uLU6hMCjMI75M1A2tKUQC"]}, "ajout par POST /playlists/{id}/items avec l'URI")
    verifie(s.scalar(select(ChampJournal).where(ChampJournal.cle == "morceau")).valeur == "Get Lucky — Daft Punk", "le morceau est noté au journal")
    verifie("déjà dans la playlist" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http), "doublon refusé, rien d'ajouté deux fois")
    verifie(len([c for c in journal if c[1].endswith("/me/playlists")]) == 1, "la playlist annuelle n'est créée qu'une fois, même pour plusieurs morceaux")
    a(2027, 7, 14, 10, 0)  # un jour d'été, aucune journée de ski : on peut quand même alimenter la playlist du week-end annuel
    nb_journees = s.query(Journee).count()
    verifie("Billie Jean" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/5ChkMS8OtdzJeqyybCc9R5", http), "/son en été : la playlist se remplit toute l'année")
    verifie(s.query(Journee).count() == nb_journees and s.scalar(select(Morceau).where(Morceau.track_id == "5ChkMS8OtdzJeqyybCc9R5")).weekend_id is None,
            "…sans créer de journée de ski ni rattacher le morceau à un week-end")
    verifie(len([c for c in journal if c[1].endswith("/me/playlists")]) == 1, "…et toujours dans la même playlist, sans en créer une seconde")
    a(2026, 12, 12, 15, 0)
    verifie("Billie Jean" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/5ChkMS8OtdzJeqyybCc9R5", http), "lien collé : le morceau est lu puis ajouté")
    def inconnu(methode, url, entetes, corps=None):
        if "/tracks/" in url:
            raise spotify.SpotifyErreur(404, "not found")
        return http(methode, url, entetes, corps)
    verifie("Je ne trouve pas" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/ZZZZZZZZZZZZZZZZZZZZZZ", inconnu), "lien inconnu (404) : message clair")
    verifie("Format" in spotify.ajouter_morceau(s, maxime, "  ", http), "/son vide : le format")

    def casse(*a, **k):
        raise RuntimeError("503")
    verifie("ne répond pas" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/BBBBBBBBBBBBBBBBBBBBBB", casse), "Spotify en panne : message clair, et pas « introuvable »")
    verifie("Il me faut un lien" in spotify.ajouter_morceau(s, maxime, "Daft Punk Get Lucky", http), "un titre écrit n'est jamais ajouté directement : on renvoie vers la recherche")
    verifie(len(s.scalars(select(Morceau)).all()) == 2, "…rien n'est rangé en base")

    # --- Blind test -----------------------------------------------------------------------------------------------
    verifie("au moins deux morceaux" in defis.lancer(s, "blind", lambda *_: "x"), "blind test : refus s'il manque des morceaux ou des auteurs")
    adr = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    jr = s.scalar(select(Journee).where(Journee.jour == date(2026, 12, 12)))
    s.add(Morceau(weekend_id=jr.weekend_id, journee_id=jr.id, personne_id=adr.id, track_id="AAAAAAAAAAAAAAAAAAAAAA", titre="X", artistes="Y"))
    s.commit()
    annonce = defis.lancer(s, "blind", lambda *_: "x")
    verifie("open.spotify.com/track/" in annonce.texte and "Daft" not in annonce.texte and "Billie" not in annonce.texte, "blind test : lien du morceau, sans révéler titre ni auteur")
    verifie({b[0][0] for b in annonce.boutons[:-1]} == {"Maxime", "Adrien"}, "…un bouton par personne qui a ajouté un morceau")
    defi = s.get(Defi, annonce.defi_id)
    auteur_id = int(defi.contenu.split(defis.SEPARATEUR)[1])
    autre = adr if auteur_id == maxime.id else maxime
    defis.voter(s, defi.id, adr, str(auteur_id))
    defis.voter(s, defi.id, maxime, str(autre.id))
    rev = defis.voter(s, defi.id, adr, "r")
    nom_auteur = s.get(Personne, auteur_id).prenom
    verifie(f"morceau de {nom_auteur}" in rev.texte, "blind test : la révélation donne l'auteur")
    bons = [p.prenom for p in (adr, maxime) if s.scalar(select(DefiVote.valeur).where(DefiVote.defi_id == defi.id, DefiVote.personne_id == p.id)) == auteur_id]
    verifie(f"Bien vu : {', '.join(bons)}" in rev.texte or "Bien vu : personne" in rev.texte, "…et ceux qui ont trouvé")
    verifie(defis.voter(s, defi.id, adr, "1") is None, "un blind test révélé est clos")

# --- Lecture : /play et /pause ------------------------------------------------------------------------------------
appels = []
etat = {"appareils": [], "erreur": None}
def http_lecture(methode, url, entetes, corps=None):
    appels.append((methode, url, corps))
    if "accounts.spotify.com" in url:
        return {"access_token": "AT2", "expires_in": 3600}
    if etat["erreur"] and "/me/player/" in url and methode == "PUT":
        raise spotify.SpotifyErreur(etat["erreur"], "x")
    if url.startswith(spotify.API + "/search"):
        return {"tracks": {"items": [{"id": "T1", "uri": "spotify:track:T1", "name": "Get Lucky", "artists": [{"name": "Daft Punk"}]}]}}
    if url.endswith("/me/player/devices"):
        return {"devices": etat["appareils"]}
    if "/tracks/" in url:
        return {"id": url.rsplit("/", 1)[1], "uri": "spotify:track:" + url.rsplit("/", 1)[1], "name": "Get Lucky", "artists": [{"name": "Daft Punk"}]}
    return {}

spotify._jeton.update(valeur=None, expire=0.0)
ecouteurs = {"id": "D1", "name": "iPhone de Maxime", "is_active": True, "is_restricted": False}
salon = {"id": "D2", "name": "Enceinte du salon", "is_active": False, "is_restricted": False}
etat["appareils"] = [ecouteurs, salon]
rep = spotify.lancer_musique("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_lecture)
play = [c for c in appels if c[0] == "PUT"][-1]
verifie("Lecture sur iPhone de Maxime : Get Lucky — Daft Punk" in rep, "/play titre : lancé sur l'appareil actif")
verifie(play[1] == spotify.API + "/me/player/play" and json.loads(play[2]) == {"uris": ["spotify:track:4uLU6hMCjMI75M1A2tKUQC"]}, "…par PUT /me/player/play avec l'URI, sans device_id : l'appareil actif est utilisé")
appels.clear()
verifie("Lecture sur iPhone de Maxime" in spotify.lancer_musique("", http_lecture) and [c for c in appels if c[0] == "PUT"][-1][2] == b"", "/play sans titre : reprend la lecture, corps vide mais présent")
appels.clear()
etat["appareils"] = [dict(salon)]
rep = spotify.lancer_musique("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_lecture)
verifie("device_id=D2" in [c for c in appels if c[0] == "PUT"][-1][1] and "Enceinte du salon" in rep, "aucun appareil actif mais un seul disponible : visé par son identifiant")
appels.clear()
etat["appareils"] = [dict(ecouteurs, is_active=False), dict(salon)]
rep = spotify.lancer_musique("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_lecture)
verifie("Plusieurs appareils" in rep and "iPhone" in rep and "Enceinte" in rep and not [c for c in appels if c[0] == "PUT"], "plusieurs appareils, aucun actif : on liste et on ne devine pas")
etat["appareils"] = []
verifie("Aucun appareil Spotify" in spotify.lancer_musique("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_lecture), "aucun appareil : on explique comment le réveiller")
etat["appareils"] = [dict(ecouteurs, is_restricted=True)]
verifie("Aucun appareil Spotify" in spotify.lancer_musique("", http_lecture), "appareil qui n'accepte aucune commande : écarté")
etat["appareils"] = [ecouteurs]
etat["erreur"] = 403
rep = spotify.lancer_musique("", http_lecture)
verifie("Premium" in rep and "spotify_autoriser" in rep, "refus 403 : Premium ou autorisation manquante, avec la marche à suivre")
etat["erreur"] = 429
verifie("attendre" in spotify.lancer_musique("", http_lecture), "limite de débit : on demande d'attendre")
etat["erreur"] = 500
verifie("ne répond pas comme prévu" in spotify.lancer_musique("", http_lecture), "autre erreur : message clair")
etat["erreur"] = None
def introuvable(m, u, e, c=None):
    if "/tracks/" in u:
        raise spotify.SpotifyErreur(404, "x")
    return http_lecture(m, u, e, c)
verifie("Je ne trouve pas" in spotify.lancer_musique("https://open.spotify.com/track/ZZZZZZZZZZZZZZZZZZZZZZ", introuvable), "lien inconnu : rien n'est lancé")
verifie("Il me faut un lien" in spotify.lancer_musique("Get Lucky", http_lecture), "un titre écrit n'est jamais lancé directement")
appels.clear()
verifie("en pause" in spotify.mettre_en_pause(http_lecture) and appels[-1][1] == spotify.API + "/me/player/pause" and appels[-1][0] == "PUT", "/pause : PUT /me/player/pause")
etat["erreur"] = 404
verifie("Rien n'est en cours" in spotify.mettre_en_pause(http_lecture), "/pause sans appareil actif : message clair")
etat["erreur"] = 403
verifie("Premium" in spotify.mettre_en_pause(http_lecture), "/pause refusée : même explication")
etat["erreur"] = None
saved = config.SPOTIFY_CLIENT_ID
config.SPOTIFY_CLIENT_ID = ""
verifie("pas branché" in spotify.lancer_musique("x", http_lecture) and "pas branché" in spotify.mettre_en_pause(http_lecture), "Spotify non configuré : /play et /pause le disent")
config.SPOTIFY_CLIENT_ID = saved

# --- Trois propositions à choisir, et file d'attente -----------------------------------------------------------------
resultats = [{"id": f"{i:022d}", "uri": f"spotify:track:{i:022d}", "name": nom, "artists": [{"name": art}], "album": {"name": alb, "release_date": date_}}
             for i, (nom, art, alb, date_) in enumerate([("Get Lucky", "Daft Punk", "Random Access Memories", "2013-05-17"),
                                                         ("Get Lucky (Radio Edit)", "Daft Punk", "Get Lucky", "2013-04-19"),
                                                         ("Get Lucky - Live", "Les Reprises", "", ""), ("Quatrième", "X", "Y", "2000")], 1)]
vus_recherche = []
def http_recherche(methode, url, entetes, corps=None):
    if "accounts.spotify.com" in url:
        return {"access_token": "AT3", "expires_in": 3600}
    vus_recherche.append(url)
    return {"tracks": {"items": resultats if "q=rien" not in url else []}}
spotify._jeton.update(valeur=None, expire=0.0)
config.SPOTIFY_CLIENT_ID = "cid"
message, boutons = spotify.proposer("Daft Punk Get Lucky", "son", 42, http_recherche)
verifie("limit=3" in vus_recherche[0] and "type=track" in vus_recherche[0], "recherche : trois résultats demandés à Spotify")
verifie(message.count("\n") == 3 and "1. Get Lucky — Daft Punk (Random Access Memories, 2013)" in message and "Quatrième" not in message, "trois propositions détaillées : titre, artiste, album, année")
verifie("3. Get Lucky - Live — Les Reprises" in message and "()" not in message, "une proposition sans album ni année n'affiche pas de parenthèses vides")
verifie([t for t, _ in boutons[0]] == ["1", "2", "3", "Aucun"] and boutons[0][0][1] == f"s|son|{resultats[0]['id']}|42", "boutons 1, 2, 3 et Aucun, qui portent l'action et la personne")
verifie(all(len(d.encode()) <= 64 for _, d in boutons[0]), "les données des boutons tiennent dans la limite de Telegram (64 octets)")
verifie("Je ne trouve pas « rien »" in spotify.proposer("rien", "son", 42, http_recherche)[0] and spotify.proposer("rien", "son", 42, http_recherche)[1] is None, "aucun résultat : message clair, pas de boutons")
verifie("Format" in spotify.proposer("  ", "son", 42, http_recherche)[0], "recherche vide : le format")
verifie("ne répond pas" in spotify.proposer("x", "son", 42, casse)[0], "Spotify en panne : message clair")

appels_file = []
def http_file(methode, url, entetes, corps=None):
    appels_file.append((methode, url))
    if "accounts.spotify.com" in url:
        return {"access_token": "AT4", "expires_in": 3600}
    if url.endswith("/me/player/devices"):
        return {"devices": etat["appareils"]}
    if "/tracks/" in url:
        return {"id": "4uLU6hMCjMI75M1A2tKUQC", "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "name": "Get Lucky", "artists": [{"name": "Daft Punk"}]}
    if etat["erreur"] and methode == "POST":
        raise spotify.SpotifyErreur(etat["erreur"], "x")
    return {}
spotify._jeton.update(valeur=None, expire=0.0)
etat["appareils"], etat["erreur"] = [ecouteurs, salon], None
rep = spotify.mettre_en_file(LIEN := "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_file)
post = [c for c in appels_file if c[0] == "POST"][-1]
verifie("Ajouté à la file d'attente sur iPhone de Maxime : Get Lucky — Daft Punk" in rep, "/file : ajouté à la file de l'appareil actif")
verifie(post[1].startswith(spotify.API + "/me/player/queue?") and "uri=spotify%3Atrack%3A4uLU6hMCjMI75M1A2tKUQC" in post[1] and "device_id" not in post[1], "…par POST /me/player/queue avec l'URI, sans device_id")
verifie(not [c for c in appels_file if c[0] == "PUT"], "…sans jamais lancer de lecture : la musique en cours n'est pas coupée")
appels_file.clear()
etat["appareils"] = [dict(salon)]
spotify.mettre_en_file(LIEN, http_file)
verifie("device_id=D2" in [c for c in appels_file if c[0] == "POST"][-1][1], "/file : un seul appareil inactif, visé par son identifiant")
etat["appareils"], etat["erreur"] = [ecouteurs], 404
verifie("lance d'abord un morceau" in spotify.mettre_en_file(LIEN, http_file), "/file sans lecture en cours (404) : on explique d'utiliser /play")
etat["erreur"] = 403
verifie("Premium" in spotify.mettre_en_file(LIEN, http_file), "/file refusé (403) : Premium ou autorisation")
etat["erreur"] = None
etat["appareils"] = []
verifie("Aucun appareil Spotify" in spotify.mettre_en_file(LIEN, http_file), "/file sans appareil : on explique comment le réveiller")
verifie("Il me faut un lien" in spotify.mettre_en_file("Get Lucky", http_file), "/file avec un titre écrit : renvoyé vers la recherche")

# --- Couche Telegram ---------------------------------------------------------------------------------------------
async def telegram():
    spotify.ajouter_morceau = lambda session, personne, texte, http=None: f"AJOUT:{texte}"
    spotify.lancer_musique = lambda texte, http=None: f"PLAY:{texte}"
    spotify.mettre_en_file = lambda texte, http=None: f"FILE:{texte}"
    spotify.mettre_en_pause = lambda http=None: "PAUSE"
    up = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    spotify.proposer = lambda texte, action, tid, http=None: (f"CHOIX:{action}:{texte}", [[("1", f"s|{action}|{'1' * 22}|{tid}"), ("Aucun", f"s|x|0|{tid}")]])
    await integrations.commande_play(up, SimpleNamespace(args=["Get", "Lucky"]))
    verifie("CHOIX:play:Get Lucky" in up.message.reply_text.call_args[0][0] and up.message.reply_text.call_args.kwargs["reply_markup"] is not None, "/play avec un titre : propositions à choisir, rien n'est lancé")
    await integrations.commande_play(up, SimpleNamespace(args=[LIEN]))
    verifie(up.message.reply_text.call_args[0][0] == f"PLAY:{LIEN}", "/play avec un lien : lancé tout de suite")
    await integrations.commande_file(up, SimpleNamespace(args=["Get", "Lucky"]))
    verifie("CHOIX:file:Get Lucky" in up.message.reply_text.call_args[0][0], "/file avec un titre : propositions à choisir")
    await integrations.commande_file(up, SimpleNamespace(args=[LIEN]))
    verifie(up.message.reply_text.call_args[0][0] == f"FILE:{LIEN}", "/file avec un lien : ajouté tout de suite")
    for action, attendu in (("play", "PLAY:spotify:track:"), ("file", "FILE:spotify:track:"), ("son", "AJOUT:spotify:track:")):
        q = SimpleNamespace(data=f"s|{action}|{'1' * 22}|1", answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=77, text="🔎 Voici…\n1. X"))
        ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await integrations.bouton_morceau(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1)), ctx)
        verifie(ctx.bot.send_message.call_args[0][1].startswith(attendu), f"choix « {action} » : l'action part avec le morceau choisi")
    q = SimpleNamespace(data=f"s|play|{'1' * 22}|1", answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=77, text="x"))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await integrations.bouton_morceau(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=2)), ctx)
    verifie(q.answer.call_args.kwargs.get("show_alert") and not ctx.bot.send_message.await_count, "choix d'une autre personne que celle qui a cherché : refusé, rien ne part")
    q = SimpleNamespace(data="s|x|0|1", answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(chat_id=77, text="x"))
    await integrations.bouton_morceau(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1)), ctx)
    verifie("annulée" in q.edit_message_text.call_args[0][0] and not ctx.bot.send_message.await_count, "« Aucun » : recherche annulée, rien ne part")
    await integrations.commande_pause(up, SimpleNamespace(args=[]))
    verifie(up.message.reply_text.call_args[0][0] == "PAUSE", "/pause depuis Telegram")
    up = SimpleNamespace(message=SimpleNamespace(text="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.lien_spotify(up, SimpleNamespace(args=[]))
    verifie("AJOUT:https://open" in up.message.reply_text.call_args[0][0], "lien collé dans le groupe : ajouté sans commande")
    up = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.commande_son(up, SimpleNamespace(args=["Get", "Lucky"]))
    verifie("CHOIX:son:Get Lucky" in up.message.reply_text.call_args[0][0], "/son avec un titre : propositions à choisir")
    config.SPOTIFY_CLIENT_ID = ""
    up = SimpleNamespace(message=SimpleNamespace(text="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.lien_spotify(up, SimpleNamespace(args=[]))
    verifie(not up.message.reply_text.await_count, "Spotify non branché : le groupe n'est pas pollué par un message à chaque lien")
    up = SimpleNamespace(message=SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.commande_ics(up, SimpleNamespace(args=[]))
    verifie(up.message.reply_document.call_args.kwargs["filename"] == "chronique-ski.ics", "/ics envoie le fichier")
    q = SimpleNamespace(data="al|2026-12-11|2", answer=AsyncMock(), edit_message_text=AsyncMock())
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    await integrations.bouton_alix(SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=4)), ctx)
    verifie("Les deux" in q.edit_message_text.call_args[0][0] and ctx.bot.send_message.call_args[0][0] == 1 and "Alix répond" in ctx.bot.send_message.call_args[0][1],
            "la réponse d'Alix est enregistrée et Maxime est prévenu")
    with SessionLocal() as s:
        verifie(amis.reglage(s, "alix:2026-12-11") == "Les deux", "…en base")

asyncio.run(telegram())
print("\nTout passe.")
