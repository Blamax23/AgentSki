"""Teste le blind test, l'horoscope, les surnoms et la gestion des archives. Spotify et modèles simulés, base jetable.
Lancer : .venv/bin/python scripts/test_blindtest.py"""
import os
import random
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "blindtest.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""
os.environ["OPENROUTER_API_KEY"] = "cle-de-test"
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, blagues, blindtest, config, conversation, horoscope, regles, spotify, surprises  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import Archive, Base, Morceau, Personne, Replique  # noqa: E402

config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET, config.SPOTIFY_REFRESH_TOKEN = "id", "secret", "refresh"
Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


# --- Comparaison des réponses ---------------------------------------------------------------------------------
verifie(blindtest.titre_principal("Get Lucky (feat. Pharrell) - Radio Edit") == "Get Lucky", "titre : (feat.) et suffixe retirés")
verifie(blindtest.verifier("c'est get lucky non ?", "Get Lucky (feat. X)", "Daft Punk, Pharrell Williams") == (True, False), "le titre est reconnu dans une phrase")
verifie(blindtest.verifier("daft punk !!", "Get Lucky", "Daft Punk, Pharrell Williams") == (False, True), "l'artiste est reconnu")
verifie(blindtest.verifier("Get Lucky de Daft Punk", "Get Lucky", "Daft Punk") == (True, True), "les deux d'un coup")
verifie(blindtest.verifier("get lukcy", "Get Lucky", "Daft Punk") == (True, False), "une faute de frappe est pardonnée")
verifie(blindtest.verifier("Beyoncé", "Halo", "Beyoncé") == (False, True) and blindtest.verifier("beyonce", "Halo", "Beyoncé")[1], "accents ignorés")
verifie(blindtest.verifier("bonne soirée tout le monde", "Get Lucky", "Daft Punk") == (False, False), "une phrase sans rapport ne trouve rien")

# --- Partie complète ------------------------------------------------------------------------------------------
with SessionLocal() as s:
    maxime = s.scalar(select(Personne).where(Personne.prenom == "Maxime"))
    adrien = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    achille = s.scalar(select(Personne).where(Personne.prenom == "Achille"))
    verifie("au moins" in blindtest.demarrer(s, 7), "blind test : trop peu de morceaux, refus clair")
    for i, (titre, art) in enumerate([("Get Lucky", "Daft Punk"), ("Halo", "Beyoncé"), ("Mr. Brightside", "The Killers"), ("Wonderwall", "Oasis")]):
        s.add(Morceau(personne_id=(adrien if i % 2 else maxime).id, track_id=f"T{i:021d}", titre=titre, artistes=art))
    s.commit()
    appels = []

    def http(methode, url, entetes, corps=None):
        appels.append((methode, url, corps))
        if "accounts.spotify.com" in url:
            return {"access_token": "jeton", "expires_in": 3600}
        if "/me/player/devices" in url:
            return {"devices": [{"id": "d1", "name": "Téléphone", "is_active": True}]}
        if "/tracks/" in url:
            return {"id": url.rsplit("/", 1)[1], "uri": "spotify:track:" + url.rsplit("/", 1)[1], "name": "x", "artists": [], "duration_ms": 200_000}
        return {}
    hasard = random.Random(1)
    verifie(blindtest.demarrer(s, 7, 3) is None and blindtest.total_manches(s, 7) == 3, "partie préparée pour 3 manches")
    verifie("déjà en cours" in blindtest.demarrer(s, 7, 3), "une seule partie à la fois par conversation")
    manche = blindtest.lancer_manche(s, 7, http, hasard)
    play = [c for c in appels if c[0] == "PUT" and "/me/player/play" in c[1]][-1]
    import json
    corps = json.loads(play[2])
    verifie(manche.numero == 1 and 30_000 <= corps["position_ms"] <= 110_000, "manche 1 lancée à un endroit au milieu du morceau")
    courant = json.loads(s.get(__import__("agentski.models", fromlist=["Reglage"]).Reglage, "blindtest:7").valeur)["courante"]
    verifie(blindtest.proposer(s, 7, adrien, "aucune idée") is None, "mauvaise réponse : silence")
    r = blindtest.proposer(s, 7, adrien, courant["artistes"].split(",")[0])
    verifie(r is not None and "l'artiste" in r.texte and not r.termine, "artiste trouvé : +1, la manche continue")
    verifie(blindtest.proposer(s, 7, achille, courant["artistes"]) is None, "l'artiste déjà trouvé ne rapporte plus rien")
    r = blindtest.proposer(s, 7, achille, courant["titre"])
    verifie(r is not None and "titre" in r.texte and r.termine, "titre trouvé après l'artiste : la manche est terminée")
    texte, fini = blindtest.reveler(s, 7)
    verifie(courant["titre"] in texte and "Scores" in texte and not fini and "Achille : 2" in texte and "Adrien : 1" in texte, "révélation : réponse et scores")
    verifie(blindtest.manche_ouverte(s, 7) is None and blindtest.reveler(s, 7) is None, "rien à révéler une fois la manche close")
    jouees = {courant["titre"]}
    for _ in range(2):
        m = blindtest.lancer_manche(s, 7, http, hasard)
        etat = json.loads(s.get(__import__("agentski.models", fromlist=["Reglage"]).Reglage, "blindtest:7").valeur)
        verifie(etat["courante"]["titre"] not in jouees, "un morceau n'est jamais rejoué dans la partie")
        jouees.add(etat["courante"]["titre"])
        texte, fini = blindtest.reveler(s, 7)
    verifie(fini and "Fin du blind test" in texte and "🥇" in texte, "dernière manche : classement final")
    verifie(not blindtest.en_cours(s, 7), "la partie est terminée et effacée")
    # Spotify sans appareil : la partie ne démarre pas
    blindtest.demarrer(s, 8, 2)
    sans_appareil = lambda m, u, e, c=None: {"devices": []} if "devices" in u else http(m, u, e, c)
    erreur = blindtest.lancer_manche(s, 8, sans_appareil, hasard)
    verifie(isinstance(erreur, str) and "Aucun appareil" in erreur and not blindtest.en_cours(s, 8), "pas d'appareil Spotify : message clair, partie annulée")
    blindtest.demarrer(s, 9, 2)
    verifie("arrêté" in blindtest.arreter(s, 9) and "Aucun" in blindtest.arreter(s, 9), "/blindtest stop")

    # --- Surnoms ----------------------------------------------------------------------------------------------
    verifie("Krok appelle Adrien « Fifou »" in amis.definir_surnom(s, "adrien", "Fifou"), "surnom donné")
    verifie(amis.nom_krok(adrien) == "Fifou" and amis.nom_krok(maxime) == "Maxime", "Krok emploie le surnom, sinon le prénom")
    verifie(amis.trouver_personne(s, "fifou").id == adrien.id, "le surnom désigne la personne")
    verifie("prénom de Achille" in amis.definir_surnom(s, "Adrien", "achille"), "un surnom ne peut pas être le prénom d'un autre")
    verifie("Je ne connais pas" in amis.definir_surnom(s, "Zébulon", "Zeb"), "personne inconnue")
    syst, msgs = conversation.composer(s, adrien, "salut", "prive", False)
    verifie("Fifou" in syst and "jamais par leur prénom" in syst and "avec Fifou" in syst, "conversation : consigne de n'employer que le surnom")
    verifie("Fifou" in amis.lister_surnoms(s) and "Maxime → (aucun)" in amis.lister_surnoms(s), "/surnom : la liste")
    verifie("Fifou" in blagues.duel(s, ["Fifou", "Achille"], lambda syst, u: "récit"), "duel : le surnom est reconnu et employé")
    verifie("Krok appellera de nouveau Adrien" in amis.definir_surnom(s, "Adrien", None) and amis.nom_krok(adrien) == "Adrien", "surnom retiré")
    verifie(regles.REGLES["surnom"].qui == "organisateur" and regles.REGLES["surnom"].lieu == "prive", "/surnom : organisateur seul, en privé")

    # --- Horoscope --------------------------------------------------------------------------------------------
    adrien.anniversaire = date(2000, 3, 25)
    achille.anniversaire = date(2001, 1, 5)
    s.commit()
    verifie(horoscope.signe(adrien) == "Bélier" and horoscope.signe(achille) == "Capricorne", "le signe vient de l'anniversaire")
    verifie(horoscope.signe(maxime) in horoscope.FAUX_SIGNES, "sans anniversaire : un faux signe, toujours le même")
    vus = []
    h1 = horoscope.horoscope(s, adrien, lambda syst, u: (vus.append(u) or "Tu croiseras un chat."), date(2026, 12, 12))
    h2 = horoscope.horoscope(s, adrien, lambda syst, u: "autre texte", date(2026, 12, 12))
    verifie("Bélier" in h1 and "Tu croiseras un chat" in h1 and h1 == h2 and len(vus) == 1, "horoscope : fixé pour la journée")
    verifie(horoscope.horoscope(s, adrien, lambda syst, u: "autre texte", date(2026, 12, 13)) != h1, "horoscope : nouveau le lendemain")
    def panne(*_):
        raise RuntimeError("modèle en panne")
    verifie("Horoscope du jour" in horoscope.horoscope(s, achille, panne, date(2026, 12, 12)), "modèle en panne : horoscope de secours")
    prompts = []
    for i, p in enumerate([maxime, adrien, achille]):
        horoscope.horoscope(s, p, lambda syst, u: (prompts.append(u) or f"Texte numéro {len(prompts)}."), date(2026, 12, 20 + i), random.Random(i))
    formes = {next(l for l in u.splitlines() if l.startswith("Forme imposée")) for u in prompts}
    verifie(len(formes) >= 2, "horoscope : la forme imposée change d'une fois à l'autre")
    verifie("Texte numéro 1." in prompts[1] and "Texte numéro 2." in prompts[2], "horoscope : les récents sont rappelés au modèle pour ne pas se répéter")
    verifie(all("Chiffre du jour" in horoscope.horoscope(s, p, panne, date(2026, 12, 20 + i)) for i, p in enumerate([maxime, adrien, achille])), "horoscope : chiffre et couleur ajoutés par le code")
    verifie("ski" in horoscope.SYSTEME and "Aucune allusion au ski" in horoscope.SYSTEME, "horoscope : consigne sans ski")

    # --- Archives ---------------------------------------------------------------------------------------------
    verifie("Aucune archive" in surprises.lister_archives(s), "/archives : rien au départ")
    a1 = Archive(auteur_id=adrien.id, cible_id=achille.id, type="photo", file_id="P1", description="Les paires", etape="ok", nb_envois=2)
    a2 = Archive(auteur_id=maxime.id, cible_id=None, type="video", file_id="V1", description="Une vidéo", etape="ok")
    a3 = Archive(auteur_id=maxime.id, etape="media")  # abandonnée en cours de route : pas listée
    s.add_all([a1, a2, a3])
    s.commit()
    s.add(Replique(archive_id=a1.id, texte="ligne"))
    s.commit()
    liste = surprises.lister_archives(s)
    verifie(f"#{a1.id}" in liste and "par Adrien" in liste and "sur Achille" in liste and "Les paires" in liste and "2 envois" in liste, "/archives : numéro, auteur, cible, description")
    verifie(f"#{a3.id}" not in liste and "personne en particulier" in liste, "/archives : les archives inachevées sont omises")
    verifie("supprimée" in surprises.retirer_archive(s, a1.id) and s.get(Archive, a1.id) is None, "/retirer_archive : supprimée")
    verifie(s.scalar(select(Replique.id)) is None, "…avec ses répliques")
    verifie("Pas d'archive" in surprises.retirer_archive(s, 999), "/retirer_archive : numéro inconnu")
    verifie(all(regles.REGLES[c].qui == "organisateur" and regles.REGLES[c].lieu == "prive" for c in ("archives", "retirer_archive")), "archives : organisateur seul, en privé")
    verifie(regles.REGLES["blindtest"].lieu == "groupe", "/blindtest : dans le groupe seulement")

    # --- Réinitialisation de la playlist --------------------------------------------------------------------
    from agentski.amis import definir_reglage, reglage
    definir_reglage(s, "playlist_annuelle", "ANCIENNE")
    avant = len(blindtest.morceaux_disponibles(s))
    texte = spotify.reinitialiser(s)
    verifie(avant > 0 and f"{avant} morceaux oubliés" in texte and reglage(s, "playlist_annuelle") is None and not blindtest.morceaux_disponibles(s), "playlist réinitialisée : id et morceaux oubliés")
    verifie("0 morceau oublié" in spotify.reinitialiser(s), "réinitialiser deux fois ne casse rien")
    verifie(regles.REGLES["reset_playlist"].qui == "organisateur" and regles.REGLES["reset_playlist"].lieu == "prive", "/reset_playlist : organisateur seul, en privé")
