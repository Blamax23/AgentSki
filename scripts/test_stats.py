"""Teste les statistiques, les chronos, /fils, /afaire, /gopro, /kit et les chiffres YouTube. Base jetable, horloge simulée.
Lancer : .venv/bin/python scripts/test_stats.py"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "stats.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, commandes_stats, config, horloge, stats, youtube  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import (Base, ChampJournal, Chute, Citation, Defi, Descente, Idee, Journee, Moment, Morceau, Note, Pari,  # noqa: E402
                             Personne, Presence, Sequence, Weekend)

Base.metadata.create_all(engine)
_dossier = Path(tempfile.mkdtemp())
config.FICHIER_PISTES = _dossier / "pistes.txt"
config.FICHIER_PISTES_APPRISES = _dossier / "apprises.txt"
config.FICHIER_PISTES.write_text("Les Grandes Platières\nSerpentine\nTimalets\nDiamant noir\n", encoding="utf-8")
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


def p(session, prenom):
    return session.scalar(select(Personne).where(Personne.prenom == prenom))


# --- Temps ---------------------------------------------------------------------------------------------
verifie([stats.lire_temps(t) for t in ("1:32", "92", "1m32", "1'32", "1min32", "2:05")] == [92, 92, 92, 92, 92, 125], "temps : toutes les écritures usuelles")
verifie(all(stats.lire_temps(t) is None for t in ("abc", "1:75", "2", "3:00:00", "99:99", "")), "temps : écritures et valeurs impossibles refusées (5 s à 30 min)")
verifie(stats.formater_temps(92) == "1:32" and stats.formater_temps(65) == "1:05", "temps : affichage m:ss")

a(2026, 12, 12, 14)
with SessionLocal() as s:
    maxime, adr, achille = p(s, "Maxime"), p(s, "Adrien"), p(s, "Achille")
    verifie("aucune journée" in stats.stats_personne(s, maxime), "stats sans journée : message clair")
    verifie("Pas encore assez" in stats.stats_tous(s), "stats de tous sans données : message clair")

    # Deux week-ends : Maxime et Adrien le 1er, Maxime et Achille le 2e
    w1 = Weekend(date_debut=date(2026, 12, 12), date_fin=date(2026, 12, 13), statut="confirme")
    w2 = Weekend(date_debut=date(2026, 12, 19), date_fin=date(2026, 12, 19), statut="confirme")
    s.add_all([w1, w2])
    s.flush()
    j1, j2, j3 = (Journee(weekend_id=w, jour=d) for w, d in ((w1.id, date(2026, 12, 12)), (w1.id, date(2026, 12, 13)), (w2.id, date(2026, 12, 19))))
    s.add_all([j1, j2, j3])
    s.flush()
    for j, qui in ((j1, (maxime, adr)), (j2, (maxime, adr)), (j3, (maxime, achille))):
        for personne in qui:
            s.add(Presence(journee_id=j.id, personne_id=personne.id))
    seq = Sequence(journee_id=j1.id)
    s.add(seq)
    s.flush()
    s.add_all([Descente(sequence_id=seq.id, piste="Serpentine", filmeur_id=maxime.id) for _ in range(5)] + [Descente(sequence_id=seq.id, piste="Timalets", filmeur_id=adr.id)])
    s.add_all([Chute(journee_id=j1.id, personne_id=adr.id) for _ in range(3)] + [Chute(journee_id=j2.id, personne_id=maxime.id)])
    s.add_all([Note(journee_id=j1.id, personne_id=maxime.id, note=6, phrase="Ça passe"), Note(journee_id=j2.id, personne_id=maxime.id, note=9, phrase="Je vole"),
               Note(journee_id=j1.id, personne_id=adr.id, note=7)])
    s.commit()

    # --- Chronos ------------------------------------------------------------------------------------------
    verifie("Format" in stats.ajouter_chrono(s, maxime, ["Serpentine"]), "/chrono incomplet : le format")
    verifie("Je ne comprends pas ce temps" in stats.ajouter_chrono(s, maxime, ["Serpentine", "vite"]), "/chrono temps illisible")
    verifie("Je ne connais pas la piste" in stats.ajouter_chrono(s, maxime, ["Inconnue", "1:30"]), "/chrono piste inconnue")
    verifie("premier chrono" in stats.ajouter_chrono(s, maxime, ["Serpentine", "1:40"]), "/chrono : premier relevé")
    verifie("nouveau record personnel" in stats.ajouter_chrono(s, maxime, ["Serpentine", "1:32"]), "/chrono : record battu")
    verifie("record à battre : 1:32" in stats.ajouter_chrono(s, maxime, ["Serpentine", "1:45"]), "/chrono : record à battre rappelé")
    verifie("Diamant noir" in stats.ajouter_chrono(s, adr, ["diamant", "noir", "2:10"]), "/chrono : piste en deux mots reconnue sans casse")
    stats.ajouter_chrono(s, adr, ["Serpentine", "1:50"])

    # --- Stats --------------------------------------------------------------------------------------------
    t = stats.stats_personne(s, maxime)
    verifie("3 jours de ski" in t and "6 descentes déclarées" in t and "1 chute, soit 0.3 par jour" in t, "stats : jours, descentes, chutes rapportées aux jours")
    verifie("note moyenne 7.5/10" in t and "de 6 à 9" in t, "stats : note moyenne et évolution première → dernière")
    verifie("Serpentine : meilleur chrono 1:32 (3 relevés, premier 1:40)" in t, "stats : meilleur chrono, relevés et premier chrono")
    t2 = stats.stats_personne(s, achille)
    verifie("1 jour de ski" in t2 and "chute" not in t2 and "note" not in t2 and "chrono" not in t2, "stats : une statistique manquante n'apparaît pas")
    tous = stats.stats_tous(s)
    verifie("jamais de classement sur Garmin" in tous and "Adrien : 2 jours · 1.5 chute par jour · note 7.0/10" in tous, "stats de tous : chutes rapportées aux jours de présence")
    verifie("Serpentine : Maxime 1:32, Adrien 1:50" in tous and "Diamant" not in tous, "stats de tous : chronos seulement sur une piste faite par au moins deux personnes")

    # --- Fils, à faire, GoPro --------------------------------------------------------------------------------------
    verifie("Rien en cours" in stats.fils(s), "/fils vide")
    s.add_all([Pari(auteur_id=maxime.id, texte="Adrien tient la journée"), Idee(auteur_id=adr.id, texte="Luge"),
               Defi(type="debat", contenu="🪑 Le débat du télésiège — Raclette ou fondue ?\n§§\nRaclette ou fondue ?"),
               Defi(type="gage", contenu="x")])
    s.commit()
    f = stats.fils(s)
    verifie("#1 Adrien tient la journée" in f and "Le débat du télésiège" in f and "1 idée pas encore ressortie" in f and "gage" not in f.lower(), "/fils : paris, défis à clore, idées")
    s.add_all([ChampJournal(journee_id=j1.id, cle="piste", valeur="Serpentine", phrase_source="x"), ChampJournal(journee_id=j1.id, cle="station", valeur="Flaine", phrase_source="x")])
    s.commit()
    af = stats.afaire(s)
    verifie("2 pistes faites sur 4" in af and "Serpentine" not in af.split("Quelques pistes")[1] and "Flaine" not in af.split("Stations")[1].split("\n")[0], "/afaire : pistes faites (journal et descentes) et stations visitées")
    verifie("Les Grandes Platières" in af and "Samoëns" in af, "/afaire : les pistes et stations restantes")
    verifie("🎥" in stats.gopro(s), "/gopro répond avec le tour de filmer")

    # --- Kit --------------------------------------------------------------------------------------------------------
    verifie(stats.kit(s, date(2030, 1, 1)) is None, "/kit d'un jour sans week-end : rien")
    s.add_all([Citation(journee_id=j1.id, personne_id=adr.id, texte="C'est une verte non ?"), Moment(journee_id=j1.id, personne_id=maxime.id, texte="Première trace", cree_a=datetime(2026, 12, 12, 8, 30))])
    s.commit()
    nom, md = stats.kit(s, date(2026, 12, 12))
    verifie(nom == "kit-2026-12-12.md" and md.startswith("# Kit du week-end du 12 au 13 décembre 2026"), "/kit : nom de fichier et titre")
    verifie(all(x in md for x in ("## Résumé", "## Journal", "## Citations", "## Compteurs de chutes", "## Notes et phrases du soir", "## Points de vue", "## Marqueurs posés avec /moment")), "/kit : les sections qui ont du contenu")
    verifie("« C'est une verte non ? »" in md and "Adrien : 3" in md and "« Je vole »" in md and "09:30" in md, "/kit : citations, chutes, phrases du soir et heure de Paris des marqueurs")
    verifie("## Morceaux ajoutés" not in md and "## Défis" not in md, "/kit : une section sans contenu n'apparaît pas")
    s.add(Morceau(weekend_id=w1.id, journee_id=j1.id, personne_id=adr.id, track_id="a" * 22, titre="Get Lucky", artistes="Daft Punk"))
    s.commit()
    verifie(stats.kit(s, None)[0] == "kit-2026-12-19.md", "/kit sans date : le dernier week-end confirmé")
    verifie("Get Lucky — Daft Punk (par Adrien)" in stats.kit(s, date(2026, 12, 12))[1], "/kit : les morceaux ajoutés")
    if "--montrer" in sys.argv:
        print("\n" + stats.kit(s, date(2026, 12, 12))[1])

# --- YouTube ---------------------------------------------------------------------------------------------------------
verifie([youtube.extraire_video(x) for x in ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ")] == ["dQw4w9WgXcQ"] * 4,
        "YouTube : liens courts, longs, shorts et identifiant nu")
verifie(youtube.extraire_video("pas une vidéo") is None, "YouTube : texte sans vidéo")
with SessionLocal() as s:
    verifie("Aucun épisode" in youtube.vues(s), "/vues sans épisode")
    verifie("Format" in youtube.enregistrer_episode(s, ["n'importe"]), "/episode mal formé : le format")
    verifie("Épisode 1" in youtube.enregistrer_episode(s, ["https://youtu.be/dQw4w9WgXcQ"]), "/episode sans titre : numéroté")
    verifie("déjà enregistré" in youtube.enregistrer_episode(s, ["https://youtu.be/dQw4w9WgXcQ"]), "/episode en double refusé")
    youtube.enregistrer_episode(s, ["https://youtu.be/aaaaaaaaaaa", "Week-end", "du", "12"])
    verifie("pas branché" in youtube.vues(s), "/vues sans clé : message clair")
    config.YOUTUBE_API_KEY = "cle"
    urls = []
    def http(url):
        urls.append(url)
        return {"items": [{"id": "dQw4w9WgXcQ", "statistics": {"viewCount": "12345", "likeCount": "321", "commentCount": "1"}}]}
    t = youtube.vues(s, http)
    verifie("12 345 vues · 321 j'aime · 1 commentaire" in t and "Week-end du 12 : introuvable" in t, "/vues : chiffres formatés, vidéo absente signalée")
    verifie("part=statistics" in urls[0] and "id=dQw4w9WgXcQ%2Caaaaaaaaaaa" in urls[0] and "key=cle" in urls[0], "/vues : un seul appel pour tous les épisodes")
    verifie("ne répond pas" in youtube.vues(s, lambda u: (_ for _ in ()).throw(OSError("réseau"))), "/vues : YouTube en panne, message clair")
    config.YOUTUBE_API_KEY = ""

# --- Couche Telegram ------------------------------------------------------------------------------------------------------
def maj(uid=1, args=()):
    msg = SimpleNamespace(text=None, reply_text=AsyncMock(), reply_document=AsyncMock())
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=uid)), SimpleNamespace(args=list(args))


async def telegram():
    up, ctx = maj(args=["Zébulon"])
    await commandes_stats.commande_stats(up, ctx)
    verifie("Je ne connais pas" in up.message.reply_text.call_args[0][0], "/stats d'un inconnu")
    up, ctx = maj()
    await commandes_stats.commande_stats(up, ctx)
    verifie("Maxime" in up.message.reply_text.call_args[0][0], "/stats seul : les siennes")
    up, ctx = maj(args=["Adrien"])
    await commandes_stats.commande_stats(up, ctx)
    verifie("Adrien" in up.message.reply_text.call_args[0][0], "/stats Adrien")
    up, ctx = maj(args=["tous"])
    await commandes_stats.commande_stats(up, ctx)
    verifie("Repères comparables" in up.message.reply_text.call_args[0][0], "/stats tous")
    up, ctx = maj(args=["Timalets", "1:40"])
    await commandes_stats.commande_chrono(up, ctx)
    verifie("Timalets" in up.message.reply_text.call_args[0][0], "/chrono depuis Telegram")
    for fonction, attendu in ((commandes_stats.commande_fils, "Ce qui est en cours"), (commandes_stats.commande_afaire, "piste"), (commandes_stats.commande_gopro, "🎥")):
        up, ctx = maj()
        await fonction(up, ctx)
        verifie(attendu in up.message.reply_text.call_args[0][0], f"{fonction.__name__} répond")
    up, ctx = maj(args=["2026-12-12"])
    await commandes_stats.commande_kit(up, ctx)
    verifie(up.message.reply_document.call_args.kwargs["filename"] == "kit-2026-12-12.md" and "montage reste à toi" in up.message.reply_document.call_args.kwargs["caption"], "/kit envoie le fichier Markdown")
    up, ctx = maj(args=["pas-une-date"])
    await commandes_stats.commande_kit(up, ctx)
    verifie("Format" in up.message.reply_text.call_args[0][0], "/kit avec une date illisible")
    up, ctx = maj(args=["2030-01-01"])
    await commandes_stats.commande_kit(up, ctx)
    verifie("Aucun week-end" in up.message.reply_text.call_args[0][0], "/kit sans week-end")
    up, ctx = maj(args=["https://youtu.be/bbbbbbbbbbb", "Test"])
    await commandes_stats.commande_episode(up, ctx)
    verifie("enregistré" in up.message.reply_text.call_args[0][0], "/episode depuis Telegram")
    up, ctx = maj()
    await commandes_stats.commande_vues(up, ctx)
    verifie("pas branché" in up.message.reply_text.call_args[0][0], "/vues sans clé depuis Telegram")

asyncio.run(telegram())
print("\nTout passe.")
