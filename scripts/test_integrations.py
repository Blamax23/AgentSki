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
    reponse = spotify.ajouter_morceau(s, maxime, "Daft Punk Get Lucky", http)
    verifie("Ajouté à la playlist : Get Lucky — Daft Punk" in reponse, "/son titre : recherche puis ajout")
    jeton = journal[0]
    verifie(jeton[1] == "https://accounts.spotify.com/api/token" and jeton[2]["Authorization"].startswith("Basic ")
            and "grant_type=refresh_token" in jeton[3].decode() and "refresh_token=refresh1" in jeton[3].decode(), "jeton renouvelé avec le refresh token, en Basic")
    verifie(config.SPOTIFY_REFRESH_TOKEN == "refresh2", "un nouveau refresh token remplace l'ancien")
    recherche = next(c for c in journal if "/search" in c[1])
    verifie("type=track" in recherche[1] and "limit=1" in recherche[1] and recherche[2]["Authorization"] == "Bearer AT", "recherche : un seul résultat, jeton porteur")
    creation = next(c for c in journal if c[1].endswith("/me/playlists"))
    verifie(json.loads(creation[3])["public"] is False and "Chronique ski" in json.loads(creation[3])["name"], "playlist créée en privé, nommée d'après le week-end")
    ajout = next(c for c in journal if c[1].endswith("/playlists/PL1/items"))
    verifie(ajout[0] == "POST" and json.loads(ajout[3]) == {"uris": ["spotify:track:4uLU6hMCjMI75M1A2tKUQC"]}, "ajout par POST /playlists/{id}/items avec l'URI")
    verifie(s.scalar(select(ChampJournal).where(ChampJournal.cle == "morceau")).valeur == "Get Lucky — Daft Punk", "le morceau est noté au journal")
    verifie("déjà dans la playlist" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http), "doublon refusé, rien d'ajouté deux fois")
    verifie(len([c for c in journal if c[1].endswith("/me/playlists")]) == 1, "la playlist du week-end n'est créée qu'une fois")
    verifie("Billie Jean" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/5ChkMS8OtdzJeqyybCc9R5", http), "lien collé : le morceau est lu puis ajouté")
    verifie("Je ne trouve pas" in spotify.ajouter_morceau(s, maxime, "https://open.spotify.com/track/ZZZZZZZZZZZZZZZZZZZZZZ", http), "lien inconnu : message clair")
    verifie("Format" in spotify.ajouter_morceau(s, maxime, "  ", http), "/son vide : le format")

    def casse(*a, **k):
        raise RuntimeError("503")
    verifie("ne répond pas" in spotify.ajouter_morceau(s, maxime, "autre titre", casse), "Spotify en panne : message clair")
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
rep = spotify.lancer_musique("Daft Punk Get Lucky", http_lecture)
play = [c for c in appels if c[0] == "PUT"][-1]
verifie("Lecture sur iPhone de Maxime : Get Lucky — Daft Punk" in rep, "/play titre : lancé sur l'appareil actif")
verifie(play[1] == spotify.API + "/me/player/play" and json.loads(play[2]) == {"uris": ["spotify:track:T1"]}, "…par PUT /me/player/play avec l'URI, sans device_id : l'appareil actif est utilisé")
appels.clear()
verifie("Lecture sur iPhone de Maxime" in spotify.lancer_musique("", http_lecture) and [c for c in appels if c[0] == "PUT"][-1][2] == b"", "/play sans titre : reprend la lecture, corps vide mais présent")
appels.clear()
etat["appareils"] = [dict(salon)]
rep = spotify.lancer_musique("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", http_lecture)
verifie("device_id=D2" in [c for c in appels if c[0] == "PUT"][-1][1] and "Enceinte du salon" in rep, "aucun appareil actif mais un seul disponible : visé par son identifiant")
appels.clear()
etat["appareils"] = [dict(ecouteurs, is_active=False), dict(salon)]
rep = spotify.lancer_musique("Get Lucky", http_lecture)
verifie("Plusieurs appareils" in rep and "iPhone" in rep and "Enceinte" in rep and not [c for c in appels if c[0] == "PUT"], "plusieurs appareils, aucun actif : on liste et on ne devine pas")
etat["appareils"] = []
verifie("Aucun appareil Spotify" in spotify.lancer_musique("Get Lucky", http_lecture), "aucun appareil : on explique comment le réveiller")
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
verifie("Je ne trouve pas" in spotify.lancer_musique("zzz", lambda m, u, e, c=None: {"tracks": {"items": []}} if "/search" in u else http_lecture(m, u, e, c)), "titre introuvable : rien n'est lancé")
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

# --- Couche Telegram ---------------------------------------------------------------------------------------------
async def telegram():
    spotify.ajouter_morceau = lambda session, personne, texte, http=None: f"AJOUT:{texte}"
    spotify.lancer_musique = lambda texte, http=None: f"PLAY:{texte}"
    spotify.mettre_en_pause = lambda http=None: "PAUSE"
    up = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.commande_play(up, SimpleNamespace(args=["Get", "Lucky"]))
    verifie(up.message.reply_text.call_args[0][0] == "PLAY:Get Lucky", "/play Get Lucky depuis Telegram")
    await integrations.commande_pause(up, SimpleNamespace(args=[]))
    verifie(up.message.reply_text.call_args[0][0] == "PAUSE", "/pause depuis Telegram")
    up = SimpleNamespace(message=SimpleNamespace(text="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.lien_spotify(up, SimpleNamespace(args=[]))
    verifie("AJOUT:https://open" in up.message.reply_text.call_args[0][0], "lien collé dans le groupe : ajouté sans commande")
    up = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
    await integrations.commande_son(up, SimpleNamespace(args=["Get", "Lucky"]))
    verifie("AJOUT:Get Lucky" in up.message.reply_text.call_args[0][0], "/son Get Lucky")
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
