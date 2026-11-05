"""Réactions-emoji de Krok sur certains messages du groupe : rares, au hasard ou sur un mot-clé, plafonnées par jour.
Pur calcul, indépendant de Telegram : le hasard est injectable."""
import random
import re

from sqlalchemy.orm import Session

from .amis import aujourdhui, definir_reglage, reglage

PROBA_HASARD = 0.03      # chance de réagir à un message quelconque
PROBA_MOT = 0.35         # chance de réagir quand un mot-clé est présent
PLAFOND_PAR_JOUR = 8     # Krok reste discret

# Seules les réactions que Telegram autorise (73 emojis : le fromage 🧀, par exemple, n'en fait pas partie).
MOTS = [
    (r"\b(raclette|tartiflette|fondue|reblochon|fromage|crozet|diots)\b", ["🌭", "😍", "💯"]),
    (r"\b(bière|bières|pression|spritz|apéro|apero|vin|champagne)\b", ["🍾", "🤝", "😎"]),
    (r"\b(chute|chuté|tombé|tombe|gamelle|vautré|plante)\b", ["🤣", "😁", "🙈"]),
    (r"\b(flemme|fatigué|crevé|dodo|dormir|sieste)\b", ["😴", "🥱"]),
    (r"\b(bravo|gg|record|champion|victoire|gagné)\b", ["🏆", "👏", "🔥"]),
    (r"\b(haha|mdr|ptdr|lol|mort de rire)\b|😂|🤣", ["🤣", "😁"]),
    (r"\b(merci|gentil)\b", ["🥰", "🤗"]),
    (r"\b(ça pique|galère|nul|dégoûté)\b", ["😢", "🫡"]),
    (r"\b(neige|poudreuse|poudre|soleil)\b", ["😍", "🤩"]),
]
AU_HASARD = ["👀", "🗿", "🤓", "😎", "🤔", "👌", "🆒", "🔥", "😁"]


def choisir(session: Session, texte: str, hasard: random.Random | None = None) -> str | None:
    """L'emoji à poser sur ce message, ou None. Tient le compteur du jour."""
    hasard = hasard or random
    cle = f"reactions:{aujourdhui()}"
    deja = int(reglage(session, cle) or 0)
    if deja >= PLAFOND_PAR_JOUR:
        return None
    emoji = None
    for motif, choix in MOTS:
        if re.search(motif, texte or "", re.I) and hasard.random() < PROBA_MOT:
            emoji = hasard.choice(choix)
            break
    else:
        if hasard.random() < PROBA_HASARD:
            emoji = hasard.choice(AU_HASARD)
    if emoji:
        definir_reglage(session, cle, str(deja + 1))
    return emoji
