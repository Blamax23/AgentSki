"""Briefing du vendredi soir, point météo du matin et récap du week-end. Cœur indépendant de Telegram.

Le modèle créatif n'a droit qu'aux faits qu'on lui donne : il ne choisit ni l'histoire ni le personnage principal."""
import random
from datetime import date, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, defis, meteo
from .amis import aujourdhui, groupe_id, reglage
from .horloge import maintenant, vers_paris
from .models import (Activite, ChampJournal, Chute, Citation, Defi, Journee, Media, Moment, Note, Personne, Presence, Reglage, Weekend)
from .rappels import milliers
from .surprises import Evenement, _une_fois

SAISON = ((11, 15), (4, 30))  # le point météo ne part qu'en saison : un oubli ne réveille personne en juillet
AVALANCHE = {1: "faible", 2: "limité", 3: "marqué", 4: "fort", 5: "très fort"}  # échelle européenne du risque d'avalanche

CONSIGNE_BRIEFING = """Tu écris le briefing du vendredi soir pour un groupe d'amis skieurs au Grand Massif, ton pote chambreur, huit lignes au plus.
Contenu : 1) deux stations à privilégier pour samedi et dimanche, avec une ligne chacune d'après les prévisions fournies (vent, neige, isotherme, altitude du village) ;
2) les deux défis proposés pour le week-end, avec une phrase chacun.
Tu n'utilises QUE les données fournies : aucune affirmation sur les pistes, les remontées, l'ouverture des domaines ou le bulletin d'avalanche.
Pour chaque défi, reprends seulement la description fournie : tu n'inventes aucune règle, aucun enjeu, aucune pénalité.
Si les prévisions ne couvrent pas un jour, dis-le au lieu de supposer. Tu ne choisis pas l'histoire du week-end ni un personnage principal.
Rappelle en une phrase que le bulletin avalanche reste à consulter. Texte brut : pas de Markdown, pas d'astérisques, pas de titres."""

CONSIGNE_RECAP = """Tu écris le récap fun d'un week-end de ski pour un groupe d'amis, ton pote chambreur, six lignes au plus.
Tu n'utilises QUE les faits fournis : n'invente aucune donnée, aucun titre, aucun nom de saison ni de numéro.
Ajoute UNE statistique absurde en reprenant tel quel un des « calculs déjà faits » fournis, sans faire toi-même aucune arithmétique ni conversion
(pas d'heures, pas de pourcentages) et sans comparaison avec un fait qui n'est pas chiffré. Une personne absente de la liste des chutes n'a « pas de chute enregistrée », pas « aucune chute ».
Tu ne choisis pas de personnage principal et tu ne tires pas l'histoire du week-end. Texte brut : pas de Markdown, pas d'astérisques."""


def en_saison(jour: date) -> bool:
    (m1, j1), (m2, j2) = SAISON
    return (jour.month, jour.day) >= (m1, j1) or (jour.month, jour.day) <= (m2, j2)


def regler_point_meteo(session: Session, personne: Personne, args: list[str]) -> str:
    if not args or args[0].lower() not in ("on", "off"):
        actuel = reglage(session, f"pointmeteo:{personne.id}")
        return f"Point météo : {'activé à ' + actuel if actuel else 'désactivé'}. Format : /pointmeteo on 07:00, ou /pointmeteo off"
    cle = f"pointmeteo:{personne.id}"
    if args[0].lower() == "off":
        r = session.get(Reglage, cle)
        if r is not None:
            session.delete(r)
            session.commit()
        return "🔕 Point météo désactivé."
    heure = args[1] if len(args) > 1 else "07:00"
    try:
        h, m = (int(x) for x in heure.replace("h", ":").split(":"))
        time(h, m)
    except ValueError:
        return "Heure invalide. Format : /pointmeteo on 07:00"
    valeur = f"{h:02d}:{m:02d}"
    r = session.get(Reglage, cle)
    if r is None:
        session.add(Reglage(cle=cle, valeur=valeur))
    else:
        r.valeur = valeur
    session.commit()
    return f"🔔 Point météo activé : chaque matin à {valeur}, en privé, du 15 novembre au 30 avril."


def texte_avalanche(session: Session) -> str:
    niveau = reglage(session, "avalanche_simule")
    if niveau:
        return f"⚠️ Risque d'avalanche : {niveau}/5 ({AVALANCHE[int(niveau)]}) — valeur SIMULÉE, pas le vrai bulletin."
    return ("Le bulletin avalanche n'est pas encore branché : il faut une clé du portail des API de Météo-France (gratuité à confirmer). "
            "En attendant, consulte le bulletin officiel avant de partir hors piste.")


# --- Récap ---------------------------------------------------------------------------------------------

def faits_recap(session: Session, weekend: Weekend) -> list[str]:
    """Ce qu'on sait sans que personne ait rien saisi de plus ; un fait absent n'apparaît pas."""
    journees = list(session.scalars(select(Journee).where(Journee.weekend_id == weekend.id, Journee.ski.is_(True)).order_by(Journee.jour)))
    ids = [j.id for j in journees]
    if not ids:
        return []
    faits = [f"{len(journees)} jour{'s' if len(journees) > 1 else ''} de ski"]
    presents = list(session.scalars(select(Personne.prenom).join(Presence, Presence.personne_id == Personne.id).where(
        Presence.journee_id.in_(ids)).distinct().order_by(Personne.id)))
    if presents:
        faits.append("présents : " + ", ".join(presents))
    chutes = session.execute(select(Personne.prenom, func.count()).select_from(Chute).join(Personne).where(
        Chute.journee_id.in_(ids)).group_by(Personne.prenom).order_by(func.count().desc())).all()
    if chutes:
        total = sum(n for _, n in chutes)
        faits.append(f"chutes : {total} au total (" + ", ".join(f"{p} {n}" for p, n in chutes) + ")")
        faits.append(f"calcul déjà fait : {total / len(journees):.1f} chute{'s' if total / len(journees) >= 2 else ''} par jour de ski")
        if presents:
            faits.append(f"calcul déjà fait : {total / len(presents):.1f} chute{'s' if total / len(presents) >= 2 else ''} par personne présente")
    for libelle, cle in (("stations", "station"), ("pistes", "piste")):
        valeurs = list(dict.fromkeys(session.scalars(select(ChampJournal.valeur).where(
            ChampJournal.journee_id.in_(ids), ChampJournal.cle == cle, ChampJournal.supprime.is_(False)))))
        if valeurs:
            faits.append(f"{libelle} : " + ", ".join(valeurs))
    citations = list(session.scalars(select(Citation.texte).where(Citation.journee_id.in_(ids))))
    if citations:
        faits.append(f"{len(citations)} citation{'s' if len(citations) > 1 else ''} culte{'s' if len(citations) > 1 else ''} : "
                     + " / ".join(f"« {c} »" for c in citations[:3]))
    moments = (session.scalar(select(func.count()).select_from(Moment).where(Moment.journee_id.in_(ids)))
               + session.scalar(select(func.count()).select_from(ChampJournal).where(
                   ChampJournal.journee_id.in_(ids), ChampJournal.cle == "moment", ChampJournal.supprime.is_(False))))
    if moments:
        faits.append(f"{moments} moment{'s' if moments > 1 else ''} marquant{'s' if moments > 1 else ''}")
    defis_lances = list(session.scalars(select(Defi.type).where(Defi.journee_id.in_(ids))))
    if defis_lances:
        faits.append("défis lancés : " + ", ".join(defis.TYPES[t][0] for t in defis_lances))
    medias = session.scalar(select(func.count()).select_from(Media).where(Media.journee_id.in_(ids)))
    if medias:
        faits.append(f"{medias} photo{'s' if medias > 1 else ''} ou vidéo{'s' if medias > 1 else ''} partagée{'s' if medias > 1 else ''}")
    fichiers = session.execute(select(func.count(), func.sum(Activite.nb_descentes), func.sum(Activite.denivele_neg)).where(Activite.journee_id.in_(ids))).one()
    if fichiers[0]:  # seulement des totaux du groupe : jamais un écart entre deux personnes
        faits.append(f"{fichiers[1]} descentes mesurées dans {fichiers[0]} fichier{'s' if fichiers[0] > 1 else ''} d'activité, {milliers(fichiers[2])} m de dénivelé négatif cumulés")
    moyenne = session.scalar(select(func.avg(Note.note)).where(Note.journee_id.in_(ids)))
    if moyenne is not None:
        faits.append(f"note moyenne du week-end : {moyenne:.1f}/10")
    return faits


def recap(session: Session, weekend: Weekend, generateur) -> str | None:
    faits = faits_recap(session, weekend)
    if not faits:
        return None
    lignes = "\n".join(f"- {f}" for f in faits)
    try:
        return "📊 " + generateur(CONSIGNE_RECAP, f"Faits du week-end :\n{lignes}")
    except Exception:
        return "📊 Récap du week-end\n" + lignes


# --- Événements du moment ------------------------------------------------------------------------------------

def evenements_meteo(session: Session, generateur, appel=None) -> list[Evenement]:
    appel = appel or meteo.appeler
    local = vers_paris(maintenant())
    jour = local.date()
    groupe = groupe_id(session)
    orga = session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))
    sortie: list[Evenement] = []

    if en_saison(jour):  # point météo du matin, en privé, pour qui l'a activé
        for r in session.scalars(select(Reglage).where(Reglage.cle.like("pointmeteo:%"))):
            personne = session.get(Personne, int(r.cle.split(":")[1]))
            if personne and personne.telegram_id and local.time() >= time(int(r.valeur[:2]), int(r.valeur[3:])) \
                    and reglage(session, f"pointmeteo_fait:{personne.id}:{jour}") is None:
                try:
                    texte = meteo.texte_point_matin(jour, appel)
                except Exception:
                    continue  # réessayé à la minute suivante
                _une_fois(session, f"pointmeteo_fait:{personne.id}:{jour}")
                sortie.append(Evenement(personne.telegram_id, texte))

    destinataire = "groupe" if groupe else (orga.telegram_id if orga else None)
    if destinataire is None:
        return sortie

    # Briefing du vendredi soir, s'il y a de la neige au programme (une journée de ski déclarée de vendredi à dimanche)
    if jour.weekday() == 4 and local.hour >= 18 and reglage(session, f"briefing:{jour}") is None:
        week_end = [jour + timedelta(days=1), jour + timedelta(days=2)]
        if session.scalar(select(Journee.id).where(Journee.jour.in_([jour, *week_end]), Journee.ski.is_(True)).limit(1)) is not None:
            try:
                donnees = meteo.donnees_briefing(week_end, appel)
                proposes = random.sample([d for d in defis.TYPES if defis.TYPES[d][3]], 2)
                texte = generateur(CONSIGNE_BRIEFING, f"Prévisions :\n{donnees}\n\nDéfis possibles :\n"
                                   + "\n".join(f"- {defis.TYPES[d][0]} : {defis.DESCRIPTIONS[d]}" for d in proposes))
            except Exception:
                texte = None  # météo ou modèle indisponible : réessayé à la minute suivante
            if texte and _une_fois(session, f"briefing:{jour}"):
                sortie.append(Evenement(destinataire, "📋 " + texte))

    # Récap fun le soir de la dernière journée de ski du week-end
    journee = session.scalar(select(Journee).where(Journee.jour == jour, Journee.ski.is_(True)))
    if journee is not None and local.time() >= time(20, 30) and reglage(session, f"recap:{journee.weekend_id}") is None:
        weekend = session.get(Weekend, journee.weekend_id)
        derniere = max(j.jour for j in weekend.journees if j.ski)
        if derniere == jour:
            texte = recap(session, weekend, generateur)
            if texte and _une_fois(session, f"recap:{weekend.id}"):
                sortie.append(Evenement(destinataire, texte))
    return sortie
