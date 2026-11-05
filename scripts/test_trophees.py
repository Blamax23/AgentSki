"""Teste les trophées : chaque famille se débloque avec les bonnes données, aucun sans donnée, annonces limitées, vitrine cachée.
Lancer : .venv/bin/python scripts/test_trophees.py"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "trophees.db")
for _cle in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "AGENDA_ICAL_URL", "YOUTUBE_API_KEY"):
    os.environ[_cle] = ""  # les tests ne touchent jamais aux vrais comptes configurés dans .env (load_dotenv ne remplace pas une variable déjà définie)
if "--reel" not in sys.argv:
    os.environ["OPENROUTER_API_KEY"] = "cle-de-test"  # un appel involontaire à OpenRouter échouerait au lieu de coûter de l'argent
os.environ["ALLOWED_USERS"] = "1:Maxime"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from agentski import amis, config, horloge, trophees  # noqa: E402
from agentski.db import SessionLocal, engine, seed_personnes, sync_allowed_users  # noqa: E402
from agentski.models import (Activite, Archive, Base, ChampJournal, Chute, Citation, Defi, DefiVote, Descente, Idee, Journee, Morceau,  # noqa: E402
                             Pari, Personne, Presence, Replique, Sequence, Trophee, Weekend)

Base.metadata.create_all(engine)
with SessionLocal() as s:
    seed_personnes(s)
    sync_allowed_users(s)


def verifie(condition, texte):
    print(("OK   " if condition else "ÉCHEC"), texte)
    if not condition:
        sys.exit(1)


def a(annee, mois, jour, h=12, m=0):
    horloge.regler(horloge.vers_utc(datetime(annee, mois, jour, h, m)))


def niveaux(s):
    return {(s.get(Personne, pid).prenom, code): n for pid, code, n in s.execute(select(Trophee.personne_id, Trophee.code, Trophee.niveau)).all()}


def haut(n, prenom, code):
    return max((v for (pr, c), v in n.items() if pr == prenom and c == code), default=0)


a(2027, 3, 1)
with SessionLocal() as s:
    P = {x.prenom: x for x in s.scalars(select(Personne))}
    m, ad, ac, ic, al = P["Maxime"], P["Adrien"], P["Achille"], P["Icham"], P["Alix"]
    verifie(trophees.calculer(s) == 0 and s.query(Trophee).count() == 0, "sans aucune journée : aucun trophée, rien d'inventé")
    verifie("Aucun trophée débloqué" in trophees.vitrine(s, m) and "47 à découvrir" in trophees.vitrine(s, m), "vitrine vide : on dit combien il y en a, sans les nommer")

    # Cinq week-ends, dont un au nouvel an ; Maxime est de tous, Adrien et Achille du premier ; Achille revient plus tard
    plan = [([date(2026, 12, 12), date(2026, 12, 13)], (m, ad, ac)), ([date(2026, 12, 19), date(2026, 12, 20)], (m, ad)),
            ([date(2026, 12, 26), date(2026, 12, 27), date(2026, 12, 28)], (m, ad)), ([date(2026, 12, 31), date(2027, 1, 1)], (m,)),
            ([date(2027, 1, 9), date(2027, 1, 10)], (m,)), ([date(2027, 2, 20)], (ac,))]
    J = {}
    for jours, qui in plan:
        w = Weekend(date_debut=jours[0], date_fin=jours[-1], statut="confirme")
        s.add(w)
        s.flush()
        for jour in jours:
            j = Journee(weekend_id=w.id, jour=jour)
            s.add(j)
            s.flush()
            J[jour] = j
            for personne in qui:
                s.add(Presence(journee_id=j.id, personne_id=personne.id))
    s.commit()

    # Chutes d'Adrien : trois le premier jour (dont une sur verte), aucune le lendemain, puis deux plus tard
    s.add_all([Chute(journee_id=J[date(2026, 12, 12)].id, personne_id=ad.id, verte=(i == 0)) for i in range(3)])
    s.add_all([Chute(journee_id=J[date(2026, 12, 19)].id, personne_id=ad.id) for _ in range(2)])
    s.add(Chute(journee_id=J[date(2026, 12, 13)].id, personne_id=ac.id))  # Achille tombe pendant son seul week-end de deux jours
    s.commit()
    trophees.calculer(s)
    n = niveaux(s)
    verifie(("Adrien", "premier_sang") in n and ("Adrien", "tombe_premier_jour") in n, "chutes : premier sang et tombé le premier jour pour la première chute de la saison")
    verifie(("Adrien", "phenix") in n and ("Adrien", "double") in n and ("Adrien", "chute_verte") in n, "chutes : Phénix, Doublé et Chute verte")
    verifie(haut(n, "Adrien", "pingouin") == 1 and ("Maxime", "pingouin") not in n, "Pingouin bronze à 5 chutes, rien pour qui n'a pas chuté")
    verifie(("Maxime", "weekend_blanc") in n and ("Adrien", "weekend_blanc") in n, "Week-end blanc : un week-end entier sans chute (Adrien l'obtient au troisième, après deux week-ends de chutes)")
    verifie(("Achille", "weekend_blanc") not in n and ("Icham", "weekend_blanc") not in n, "Week-end blanc : pas pour qui est tombé pendant son seul week-end, ni pour qui n'est pas venu")
    verifie(("Maxime", "incassable") in n, "Incassable : cinq jours de ski d'affilée sans chute")
    verifie(("Maxime", "premiere_trace") in n and ("Achille", "premiere_trace") in n and ("Alix", "premiere_trace") not in n, "Première trace : les présents du premier jour de la saison")
    verifie(haut(n, "Maxime", "fidele") == 1 and haut(n, "Maxime", "infatigable") == 1, "Fidèle bronze à 11 jours, Infatigable bronze à 3 jours d'affilée")
    verifie(("Maxime", "jamais_deux") in n and ("Adrien", "jamais_deux") in n and ("Achille", "jamais_deux") not in n, "Jamais deux sans trois : trois semaines de suite, même autour du nouvel an")
    verifie(("Achille", "revenant") in n and ("Maxime", "revenant") not in n, "Le revenant : retour après plus d'un mois")
    verifie(("Maxime", "nouvel_an") in n and ("Adrien", "nouvel_an") not in n, "Nouvel An sur les skis : seulement pour qui skiait ce jour-là")
    verifie(all((x, "invite_honneur") in n for x in ("Maxime", "Adrien", "Achille")) and ("Icham", "invite_honneur") not in n, "Invité d'honneur : tous ceux qui sont venus, pas les autres")

    # POV, journal, groupe
    seq = Sequence(journee_id=J[date(2026, 12, 12)].id)
    s.add(seq)
    s.flush()
    s.add_all([Descente(sequence_id=seq.id, piste="Serpentine", filmeur_id=m.id) for _ in range(10)])
    for jour in (date(2026, 12, 12), date(2026, 12, 19), date(2026, 12, 26)):
        s.add(ChampJournal(journee_id=J[jour].id, cle="moment", valeur="Adrien perd un ski", phrase_source="x"))
    s.add_all([Citation(journee_id=J[date(2026, 12, 12)].id, personne_id=ad.id, texte=f"phrase {i}") for i in range(5)])
    s.add_all([Idee(auteur_id=ac.id, texte=f"idée {i}") for i in range(5)] + [Pari(auteur_id=ic.id, texte=f"pari {i}", gagnant_id=ic.id if i == 0 else None, statut="clos" if i == 0 else "ouvert") for i in range(3)])
    amis.definir_reglage(s, f"rdv_count:{m.id}", "5")
    for i in range(10):
        amis.definir_reglage(s, f"pointmeteo_fait:{m.id}:2026-12-{10 + i}", "1")
    amis.definir_reglage(s, f"chambre_count:{m.id}", "10")
    amis.definir_reglage(s, f"monotitre:{ic.id}", "1")
    s.commit()

    # Défis
    def defi(type_, tire, votes, jour=None, etat="en_cours", contenu="x"):
        d = Defi(type=type_, tire_id=tire.id if tire else None, journee_id=J[jour].id if jour else None, etat=etat, contenu=contenu)
        s.add(d)
        s.flush()
        for pid, v in votes:
            s.add(DefiVote(defi_id=d.id, personne_id=pid, valeur=v))
        return d
    for _ in range(5):
        defi("gage", ac, [(m.id, 1), (ad.id, 1), (al.id, 0)])
    defi("chef", al, [(m.id, 9), (ad.id, 8), (ic.id, 9)])
    for _ in range(3):
        defi("dj", ic, [(m.id, 9), (ad.id, 8)])
    for jour in list(J)[:5]:
        defi("debat", None, [], jour=jour, etat="clos")
    defi("proces", ad, [(m.id, 0), (ac.id, 0), (al.id, 1)])
    for _ in range(3):
        defi("proces", ac, [(m.id, 1), (ad.id, 1), (al.id, 0)])
    for i in range(3):
        d = defi("pronostic", None, [(m.id, 1)], etat="clos")
        amis.definir_reglage(s, f"pronostic_resultat:{d.id}", "oui")
    d = defi("pronostic", None, [(m.id, 1)], etat="clos")
    amis.definir_reglage(s, f"pronostic_resultat:{d.id}", "non")  # un pronostic raté
    s.commit()
    trophees.calculer(s)
    n = niveaux(s)
    verifie(("Achille", "premier_defi") in n and haut(n, "Achille", "gage") == 1, "Premier défi et Gagé bronze : cinq gages validés par la majorité")
    verifie(("Alix", "chef_etoile") in n and ("Icham", "dj_resident") in n, "Chef étoilé et DJ résident : moyennes de 8 ou plus")
    verifie(haut(n, "Maxime", "debatteur") == 1, "Débatteur bronze : cinq débats tranchés en sa présence")
    verifie(("Adrien", "avocat_diable") in n and haut(n, "Achille", "coupable") == 1, "Avocat du diable (acquitté) et Coupable bronze (trois condamnations)")
    verifie(haut(n, "Maxime", "madame_irma") == 1, "Madame Irma bronze : trois pronostics justes sur quatre, le raté n'est pas compté")
    verifie(("Maxime", "cameraman") in n and haut(n, "Maxime", "cameraman") == 1, "Caméraman bronze : 10 descentes filmées")
    verifie(("Adrien", "vedette") in n and ("Maxime", "vedette") not in n, "Vedette : cité dans les moments de trois week-ends de suite")
    verifie(haut(n, "Adrien", "poete") == 1 and haut(n, "Achille", "idealiste") == 1 and haut(n, "Icham", "parieur") == 1 and ("Icham", "pari_tenu") in n, "Poète, Idéaliste, Parieur et Pari tenu")
    verifie(haut(n, "Maxime", "rendez_vous") == 1 and haut(n, "Maxime", "meteorologue") == 1 and haut(n, "Maxime", "chambreur") == 1, "Rendez-vous, Météorologue et Chambreur : les compteurs")
    verifie(("Icham", "mono_titre") in n and ("Maxime", "mono_titre") not in n, "Mono-titre : le même morceau ajouté deux fois")

    # Archives, morceaux, activités, journal
    arch = [Archive(auteur_id=al.id, cible_id=ad.id, type="photo", file_id="F", description="d", etape="ok") for _ in range(5)]
    s.add_all(arch)
    s.flush()
    s.add_all([Replique(archive_id=arch[0].id, texte=f"ligne {i}") for i in range(5)])
    s.add_all([Morceau(weekend_id=1, journee_id=J[date(2026, 12, 12)].id, personne_id=al.id, track_id=f"{i:022d}", titre=f"T{i}", artistes="A") for i in range(10)])
    def activite(jour, nb, deniv, vmax, debut, fin, empreinte):
        s.add(Activite(personne_id=m.id, journee_id=J[jour].id, source="fit", empreinte=empreinte, debut=debut, duree_s=3600, nb_descentes=nb, denivele_neg=deniv,
                       vitesse_max=vmax, premiere_descente=debut, derniere_descente=fin))
    activite(date(2026, 12, 12), 46, 3100, 50.0, datetime(2026, 12, 12, 7, 30), datetime(2026, 12, 12, 16, 0), "a")
    activite(date(2026, 12, 13), 20, 3000, 60.0, datetime(2026, 12, 13, 9, 0), datetime(2026, 12, 13, 14, 0), "b")
    activite(date(2026, 12, 19), 10, 2800, 55.0, datetime(2026, 12, 19, 9, 0), datetime(2026, 12, 19, 13, 0), "c")
    for st in config.STATIONS:
        s.add(ChampJournal(journee_id=J[date(2026, 12, 13)].id, cle="station", valeur=st, phrase_source="x"))
    s.add_all([ChampJournal(journee_id=J[date(2026, 12, 19)].id, cle="piste", valeur="Cascades", phrase_source="x"),
               ChampJournal(journee_id=J[date(2026, 12, 20)].id, cle="piste", valeur="Les Grandes Platières", phrase_source="x")])
    s.commit()
    trophees.calculer(s)
    n = niveaux(s)
    verifie(haut(n, "Alix", "archiviste") == 1 and haut(n, "Adrien", "cible_favorite") == 1 and haut(n, "Alix", "melomane") == 1, "Archiviste, Cible favorite (envois) et Mélomane")
    verifie(haut(n, "Maxime", "kilometre_vertical") == 3 and haut(n, "Maxime", "marathonien") == 3, "Kilomètre vertical et Marathonien : or, d'après la meilleure journée")
    verifie(("Maxime", "everest") in n, "Everest : 8 900 m de dénivelé négatif cumulés")
    verifie(("Maxime", "fusee") in n and ("Maxime", "leve_tot") in n and ("Maxime", "dernier_remonte") in n, "Fusée, Lève-tôt (8 h 30 à Paris) et Dernier remonté (17 h à Paris)")
    verifie(("Maxime", "grand_tour") in n and ("Maxime", "explorateur") in n and ("Adrien", "explorateur") in n and ("Icham", "explorateur") not in n, "Grand tour et Explorateur : les cinq stations, le même jour, pour les présents ce jour-là seulement")
    verifie(("Maxime", "la_cascade") in n and ("Maxime", "tout_en_haut") in n, "La Cascade et Tout en haut")
    verifie(not any(c in ("kilometre_vertical", "everest", "fusee") for (pr, c) in n if pr == "Achille"), "aucun trophée de fichiers pour qui n'en a pas envoyé : jamais comparé")
    verifie(haut(n, "Maxime", "vitrine_pleine") == 1, "Vitrine pleine bronze : au moins 15 trophées différents")

    # Idempotence et annonces
    avant = s.query(Trophee).count()
    verifie(trophees.calculer(s) == 0 and s.query(Trophee).count() == avant, "un second calcul ne débloque rien de plus")
    verifie(not any(c not in {c2 for c2, *_ in trophees.CATALOGUE} | {"vitrine_pleine"} for (_, c) in n), "tous les codes viennent du catalogue")

    # Annonces : on repart d'une table vide avec des trophées non annoncés, et le calcul est neutralisé pour ne tester que l'annonce
    s.query(Trophee).delete()
    s.commit()
    vrai_calcul = trophees.calculer
    trophees.calculer = lambda session: 0
    horloge.regler(horloge.vers_utc(datetime(2027, 3, 1, 12, 0)))
    amis.definir_reglage(s, "groupe_id", "-100")
    ad_ = s.scalar(select(Personne).where(Personne.prenom == "Adrien"))
    s.add_all([Trophee(personne_id=ad_.id, code="pingouin", niveau=2), Trophee(personne_id=ad_.id, code="fidele", niveau=1),
               Trophee(personne_id=ad_.id, code="melomane", niveau=1), Trophee(personne_id=ad_.id, code="poete", niveau=2),
               Trophee(personne_id=ad_.id, code="vitrine_pleine", niveau=1)])
    s.commit()
    amis.definir_reglage(s, "trophees_dernier", "2000-01-01T00:00:00")
    evs = trophees.evenements(s)
    verifie(len(evs) == 3 and all(e.destinataire == "groupe" for e in evs), "annonces : trois au plus par passage, dans le groupe")
    verifie("🏆 Adrien débloque « Pingouin » 🥈 : 15 chutes dans la saison" in evs[0].texte, "annonce d'un niveau : nom, médaille, seuil et description")
    verifie("« Fidèle » 🥉 : 10 jours de ski" in evs[1].texte, "annonce : un seuil à plusieurs chiffres s'écrit sans virgule parasite")
    verifie(trophees.evenements(s) == [], "annonces : un second passage dans les 5 minutes ne fait rien")
    horloge.regler(horloge.vers_utc(datetime(2027, 3, 1, 12, 6)))
    suite = trophees.evenements(s)
    verifie(len(suite) == 2 and "Vitrine pleine" in suite[1].texte and "15 trophées différents" in suite[1].texte, "annonces : le reste part au passage suivant, Vitrine pleine comprise")
    verifie(s.query(Trophee).filter(Trophee.annonce.is_(False)).count() == 0, "tout est annoncé une seule fois")

    t = trophees.vitrine(s, ad_)
    verifie("sur 47, le reste est caché" in t and "🥈 Pingouin" in t and "🥈 Poète" in t and "🥉 Fidèle" in t, "vitrine : ses trophées avec leur médaille, le reste caché")

    # Sans groupe : l'organisateur est prévenu en privé
    s.query(amis.Reglage).filter(amis.Reglage.cle == "groupe_id").delete()
    s.add(Trophee(personne_id=ad_.id, code="idealiste", niveau=1))
    s.commit()
    amis.definir_reglage(s, "trophees_dernier", "2000-01-01T00:00:00")
    s.get(Personne, m.id).telegram_id = 1
    s.commit()
    evs = trophees.evenements(s)
    verifie(len(evs) == 1 and evs[0].destinataire == 1, "sans groupe enregistré : l'annonce part en privé à l'organisateur")

trophees.calculer = vrai_calcul

# --- Couche Telegram -------------------------------------------------------------------------------------------------
import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from agentski import commandes_stats  # noqa: E402

up = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_user=SimpleNamespace(id=1))
asyncio.run(commandes_stats.commande_trophees(up, SimpleNamespace(args=[])))
verifie("Tes trophées" in up.message.reply_text.call_args[0][0] or "Aucun trophée" in up.message.reply_text.call_args[0][0], "/trophees répond avec la vitrine de la personne")
print("\nTout passe.")
