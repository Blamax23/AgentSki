"""Le « bonne nuit » personnalisé de Krok : un message privé à chaque personne présente un jour de ski, le soir, une seule fois."""
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import conversation
from .amis import aujourdhui, definir_reglage, nom_krok, reglage
from .horloge import maintenant, vers_paris
from .models import Chute, Journee, Moment, Personne, Presence
from .surprises import Evenement

HEURE_MINI = 22.5   # 22h30 : pas avant, la soirée n'est pas finie

SYSTEME = """Tu es Krok, un bot, et tu souhaites bonne nuit en message privé à un ami après une journée de ski au Grand Massif.
Deux ou trois phrases, chaleureuses et un peu taquines, sans liste. Tu t'appuies sur un ou deux faits de sa journée fournis plus bas (jamais plus), sans rien inventer, et sur ce que tu ressens. Tu tutoies, tu finis par une façon de souhaiter bonne nuit, un emoji au plus."""

SECOURS = ["Bonne nuit {p} 😴 Recharge bien, demain on remet ça.",
           "Allez, dodo {p}. Je veille sur le groupe, toi tu veilles sur ton sommeil 🌙",
           "Bonne nuit {p} ! À demain, et pas de ronflements 😁"]


def _faits(session: Session, personne: Personne, journee: Journee) -> list[str]:
    chutes = session.scalar(select(func.count()).select_from(Chute).where(Chute.journee_id == journee.id, Chute.personne_id == personne.id))
    moments = session.scalar(select(func.count()).select_from(Moment).where(Moment.journee_id == journee.id, Moment.personne_id == personne.id))
    presents = list(session.scalars(select(Personne.prenom).join(Presence, Presence.personne_id == Personne.id).where(Presence.journee_id == journee.id)))
    faits = [f"Il y avait {len(presents)} personne{'s' if len(presents) > 1 else ''} aujourd'hui : {', '.join(presents)}."]
    if chutes:
        faits.append(f"{personne.prenom} a noté {chutes} chute{'s' if chutes > 1 else ''}.")
    if moments:
        faits.append(f"{personne.prenom} a marqué {moments} moment{'s' if moments > 1 else ''} fort{'s' if moments > 1 else ''}.")
    return faits


def evenements(session: Session, generateur) -> list[Evenement]:
    """Les bonne nuit à envoyer maintenant (après 22h30, une fois par personne et par jour de ski)."""
    local = vers_paris(maintenant())
    if local.hour + local.minute / 60 < HEURE_MINI:
        return []
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
    if journee is None:
        return []
    sortie = []
    for p in session.scalars(select(Personne).join(Presence, Presence.personne_id == Personne.id).where(Presence.journee_id == journee.id).order_by(Personne.id)):
        cle = f"bonnenuit:{aujourdhui()}:{p.id}"
        if p.telegram_id is None or reglage(session, cle) is not None:
            continue
        definir_reglage(session, cle, "1")  # une seule fois, même si l'envoi échoue : pas de doublon à la minute suivante
        fiche = conversation._resume(session, p, "prive")  # message privé : la fiche privée peut servir
        demande = (f"Prénom : {nom_krok(p)}.\nTon humeur : {conversation.humeur(session)}.\nFaits de sa journée :\n- " + "\n- ".join(_faits(session, p, journee))
                   + (f"\nCe que tu sais de lui ou elle :\n{fiche}" if fiche else "") + "\nÉcris le message.")
        try:
            texte = generateur(SYSTEME, demande).strip()
        except Exception:
            texte = ""
        sortie.append(Evenement(p.telegram_id, texte or random.choice(SECOURS).format(p=nom_krok(p))))
    return sortie
