"""Où et quand chaque commande est permise : en privé seulement, dans le groupe seulement, pendant un week-end de ski seulement,
ou réservée à l'organisateur. Tout est dans la table REGLES ci-dessous : une commande absente de la table marche partout, toujours.

Un seul garde-fou central, placé avant tous les autres gestionnaires : il explique pourquoi la commande est refusée et l'arrête."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from . import acces, config
from .amis import aujourdhui
from .db import SessionLocal
from .models import Weekend


@dataclass(frozen=True)
class Regle:
    lieu: str = "partout"     # partout | prive | groupe
    quand: str = "toujours"   # toujours | ski : seulement pendant un week-end de ski déclaré
    qui: str = "tous"         # tous | organisateur


PRIVE = Regle(lieu="prive")
GROUPE = Regle(lieu="groupe")
SKI = Regle(quand="ski")

# Les valeurs ci-dessous sont des propositions, à ajuster ici.
REGLES: dict[str, Regle] = {
    # Ce qui est personnel, ou doit rester une surprise : en privé seulement
    "easter": PRIVE, "ajout": PRIVE, "mesajouts": PRIVE, "retirer": PRIVE, "capsule": PRIVE, "pointmeteo": PRIVE,
    "anniversaire": PRIVE, "oublie": PRIVE,
    # Ce qui se joue à plusieurs : dans le groupe seulement
    "duel": GROUPE, "quicestce": GROUPE, "adrille_rallye": GROUPE, "adrille": GROUPE,
    # Ce qui n'a de sens que pendant un week-end de ski déclaré
    "moment": SKI, "chute": SKI, "chrono": SKI, "ou": SKI, "rdv": SKI, "gopro": SKI, "play": SKI, "file": SKI, "pause": SKI,
    # Ce qui engage l'organisateur seul
    "groupe": Regle(qui="organisateur"), "revelation": Regle(qui="organisateur"),
}


def en_week_end_de_ski(session, jour: date | None = None) -> bool:
    """Vrai si aujourd'hui tombe dans un week-end de ski confirmé (celui que la question du jeudi a créé)."""
    jour = jour or aujourdhui()
    return session.scalar(select(Weekend.id).where(Weekend.statut == "confirme", Weekend.date_debut <= jour, Weekend.date_fin >= jour).limit(1)) is not None


def commande_de(update: Update) -> str | None:
    """Nom de la commande d'un message (« /duel@bot Adrien » donne « duel »), ou None si ce n'est pas une commande."""
    texte = update.message.text if update.message and update.message.text else ""
    if not texte.startswith("/"):
        return None
    return texte.split()[0][1:].split("@")[0].lower()


def refus(regle: Regle, prive: bool, ski: bool, organisateur: bool) -> str | None:
    """La phrase qui explique le refus, ou None si la commande est permise."""
    if regle.qui == "organisateur" and not organisateur:
        return "Cette commande est réservée à l'organisateur."
    if regle.lieu == "prive" and not prive:
        return "Celle-ci se lance en privé avec moi : écris-moi directement."
    if regle.lieu == "groupe" and prive:
        return "Celle-ci se joue dans le groupe, pas en privé."
    if regle.quand == "ski" and not ski:
        return "Cette commande ne marche que pendant un week-end de ski (celui que la question du jeudi déclare). Rien n'est enregistré."
    return None


async def garde(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nom = commande_de(update)
    regle = REGLES.get(nom) if nom else None
    if regle is None:
        return
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id) if update.effective_user else None
        phrase = refus(regle, update.effective_chat.type == "private", en_week_end_de_ski(session) if regle.quand == "ski" else True,
                       personne is not None and personne.prenom == config.ORGANISATEUR)
    if phrase:
        await update.message.reply_text(phrase)
        raise ApplicationHandlerStop


def installer(app) -> None:
    app.add_handler(TypeHandler(Update, garde), group=-2)  # après la liste blanche (-3), avant les commandes (0)
