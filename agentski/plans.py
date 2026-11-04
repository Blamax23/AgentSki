"""Idées de plans, paris, liste de courses et suggestions avec recherche web. Cœur indépendant de Telegram."""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .amis import _maintenant_aware, aujourdhui, personne_par_prenom
from .models import Idee, Journee, Pari, Personne, Plat, Presence

DEFAUT_PRESENTS = 5


# --- Idées ------------------------------------------------------------------------------

def ajouter_idee(session: Session, auteur: Personne, texte: str) -> str:
    texte = texte.strip()
    if not texte:
        return "Format : /idee une soirée raclette et cartes"
    idee = Idee(auteur_id=auteur.id, texte=texte, cree_a=_maintenant_aware())
    session.add(idee)
    session.commit()
    return f"💡 Idée #{idee.id} dans la boîte. Je la ressortirai dans un prochain rappel du matin."


def idees_pour_rappel(session: Session, limite: int = 3) -> list[str]:
    """Les plus anciennes idées pas encore ressorties ; chacune n'est reprise qu'une fois."""
    idees = list(session.scalars(select(Idee).where(Idee.rappelee.is_(False)).order_by(Idee.id).limit(limite)))
    for i in idees:
        i.rappelee = True
    session.commit()
    return [i.texte for i in idees]


# --- Paris ---------------------------------------------------------------------------------

def ajouter_pari(session: Session, auteur: Personne, texte: str) -> str:
    texte = texte.strip()
    if not texte:
        return "Format : /pari Adrien ne tombera pas de la journée"
    pari = Pari(auteur_id=auteur.id, texte=texte, cree_a=_maintenant_aware())
    session.add(pari)
    session.commit()
    return f"🎲 Pari #{pari.id} enregistré : {texte}\nQuand il est tranché : /resolu {pari.id} Prénom-du-gagnant"


def paris_ouverts(session: Session) -> str:
    paris = list(session.scalars(select(Pari).where(Pari.statut == "ouvert").order_by(Pari.id)))
    if not paris:
        return "Aucun pari en cours. /pari pour en lancer un."
    return "🎲 Paris en cours :\n" + "\n".join(f"#{p.id} · {p.texte}" for p in paris)


def resoudre_pari(session: Session, numero: int, gagnant: str) -> str:
    pari = session.get(Pari, numero)
    if pari is None or pari.statut != "ouvert":
        return f"Pas de pari ouvert #{numero}."
    personne = personne_par_prenom(session, gagnant)
    if personne is None:
        return f"Je ne connais pas « {gagnant} »."
    pari.statut, pari.gagnant_id = "clos", personne.id
    session.commit()
    return f"🏆 Pari #{numero} tranché : {personne.prenom} gagne. « {pari.texte} »"


# --- Courses -----------------------------------------------------------------------------------

def nombre_de_presents(session: Session) -> int | None:
    """Présents d'aujourd'hui, sinon de la prochaine journée de ski connue ; None si on ne sait pas."""
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
    if journee is None:
        journee = session.scalar(select(Journee).where(Journee.jour > aujourdhui(), Journee.ski.is_(True)).order_by(Journee.jour))
    if journee is None:
        return None
    return session.scalar(select(func.count()).select_from(Presence).where(Presence.journee_id == journee.id)) or None


def ajouter_plat(session: Session, auteur: Personne, texte: str) -> str:
    texte = texte.strip()
    session.add(Plat(auteur_id=auteur.id, texte=texte, cree_a=_maintenant_aware()))
    session.commit()
    total = session.scalar(select(func.count()).select_from(Plat).where(Plat.actif.is_(True)))
    return f"🍽 Ajouté à la liste de courses : {texte} ({total} plat{'s' if total > 1 else ''}). /course pour la liste."


def vider_courses(session: Session) -> str:
    for plat in session.scalars(select(Plat).where(Plat.actif.is_(True))):
        plat.actif = False
    session.commit()
    return "🧺 Liste de courses vidée."


CONSIGNE_COURSES = ("Tu prépares UNE seule liste de courses pour un week-end au chalet. Regroupe tous les plats, additionne les ingrédients "
                    "communs, classe par rayon, donne des quantités raisonnables pour le nombre de personnes et respecte les précisions "
                    "(régimes, exclusions). Réponds uniquement par la liste, une ligne par article, sans introduction.")


def liste_courses(session: Session, generateur, nb: int | None = None) -> str:
    plats = [p.texte for p in session.scalars(select(Plat).where(Plat.actif.is_(True)).order_by(Plat.id))]
    if not plats:
        return "Aucun plat pour l'instant. Ajoute-en avec /course raclette, /course tartiflette sans lardons…"
    nb = nb or nombre_de_presents(session)
    note = "" if nb else f"\n(Je ne sais pas encore combien vous serez : calcul pour {DEFAUT_PRESENTS}.)"
    nb = nb or DEFAUT_PRESENTS
    try:
        liste = generateur(CONSIGNE_COURSES, f"Nombre de personnes : {nb}.\nPlats :\n" + "\n".join(f"- {p}" for p in plats))
    except Exception:
        return "Je n'ai pas réussi à calculer la liste, réessaie dans un instant. Les plats sont gardés."
    return f"🛒 Courses pour {nb} personne{'s' if nb > 1 else ''} (quantités estimées) :\n{liste}{note}"


# --- Suggestions avec recherche web --------------------------------------------------------------------

def anonymiser(session: Session, texte: str) -> str:
    """La recherche web n'est pas couverte par le ZDR : aucun prénom du groupe ne doit partir dans la demande."""
    for prenom in session.scalars(select(Personne.prenom)):
        texte = re.sub(rf"\b{re.escape(prenom)}\b", "un ami", texte, flags=re.I)
    return texte


def suggestion(session: Session, theme: str, recherche) -> str:
    theme = anonymiser(session, theme.strip()) or "une soirée entre amis au chalet"
    demande = (f"Propose 3 idées concrètes pour un groupe d'amis skieurs au Grand Massif (Haute-Savoie) : {theme}. "
               "Une ou deux lignes chacune, avec le lieu ou le prix si tu les trouves. Réponds en français.")
    try:
        texte, urls = recherche(demande)
    except Exception:
        return "La recherche n'a pas abouti, réessaie dans un instant."
    sources = "\n".join(f"• {u}" for u in urls)
    return f"💡 {texte}" + (f"\n\nSources :\n{sources}" if sources else "")
