"""L'horoscope de Krok : absurde, fixé pour la journée, sans le moindre rapport avec le ski. Cœur indépendant de Telegram.

Le signe vient de l'anniversaire s'il est connu ; sinon Krok attribue un faux signe, toujours le même pour la même personne."""
from datetime import date

from sqlalchemy.orm import Session

from .amis import aujourdhui, definir_reglage, nom_krok, reglage
from .models import Personne

SIGNES = [  # (premier jour du signe : mois, jour), nom
    ((1, 20), "Verseau"), ((2, 19), "Poissons"), ((3, 21), "Bélier"), ((4, 20), "Taureau"), ((5, 21), "Gémeaux"), ((6, 21), "Cancer"),
    ((7, 23), "Lion"), ((8, 23), "Vierge"), ((9, 23), "Balance"), ((10, 23), "Scorpion"), ((11, 22), "Sagittaire"), ((12, 22), "Capricorne"),
]
FAUX_SIGNES = ["Reblochon", "Raclette", "Tartiflette", "Fondue", "Croziflette", "Diots", "Beaufort", "Tomme"]

SYSTEME = """Tu es Krok, le bot d'un groupe d'amis, et tu rédiges l'horoscope du jour d'une personne.
Ton horoscope est absurde, faussement solennel, taquin et jamais méchant. Quatre phrases au plus : la prédiction du jour, un conseil inutile, puis « Chiffre porte-bonheur : … » et « Couleur du jour : … » en une ligne.
Tu n'inventes aucun fait réel sur la personne (famille, santé, argent, amours, travail). Aucune allusion au ski, à la neige, aux pistes ni aux chutes. Tutoiement, un emoji au plus."""

SECOURS = ["Aujourd'hui, les astres hésitent entre te sourire et regarder ailleurs. Fais comme eux. Chiffre porte-bonheur : 7. Couleur du jour : beige.",
           "Une surprise t'attend, mais elle est petite et elle est dans ta poche. Chiffre porte-bonheur : 3. Couleur du jour : moutarde."]


def signe(personne: Personne) -> str:
    if personne.anniversaire is None:
        return FAUX_SIGNES[personne.id % len(FAUX_SIGNES)]
    jour = (personne.anniversaire.month, personne.anniversaire.day)
    nom = SIGNES[-1][1]  # avant le 20 janvier : Capricorne
    for debut, libelle in SIGNES:
        if jour >= debut:
            nom = libelle
    return nom


def horoscope(session: Session, personne: Personne, generateur, jour: date | None = None) -> str:
    """L'horoscope du jour : le même toute la journée pour la même personne."""
    jour = jour or aujourdhui()
    cle = f"horoscope:{personne.id}:{jour}"
    deja = reglage(session, cle)
    if deja:
        return deja
    nom = nom_krok(personne)
    mon_signe = signe(personne)
    try:
        corps = generateur(SYSTEME, f"Personne : {nom}.\nSigne : {mon_signe}.\nDate : {jour:%d/%m/%Y}.\nÉcris l'horoscope.").strip()
    except Exception:
        corps = ""
    texte = f"🔮 Horoscope du jour pour {nom} ({mon_signe}) :\n{corps or SECOURS[personne.id % len(SECOURS)]}"
    definir_reglage(session, cle, texte)
    return texte
