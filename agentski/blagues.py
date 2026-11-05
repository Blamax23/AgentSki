"""Petites fonctions drôles : /preaud, /icham, /adrille_rallye, /duel, /quicestce. Cœur indépendant de Telegram.

Le bot s'appelle Krok. Le vainqueur d'un duel est tiré au sort par le code, jamais par le modèle."""
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import defis
from .models import Citation, Devinette, DevinetteReponse, Personne

PREAUD = ("Moi, Krok le bot, voici mon classement des Preaud préférés : \n1. Roland\n2. Patrick\n3. Marion\n4. La PS4 d'Achille\n5. Alix\n6. Hercule Preaud (le futur fils d'Achille)\n7. Préau de ma cour d'école\n8. Achille")

SITE = "https://adrille-rallye.vercel.app"
ACCROCHES = ["Une annonce d'intérêt général, sortez les casques.", "Pause pub, mais une pub avec du moteur dedans.",
             "Krok recommande, et Krok ne recommande pas grand-chose.", "Vous voulez voir vos passages en rallye ? Écoutez bien.",
             "Message sponsorisé par personne, mais ça mérite le détour."]
# Rien d'autre que ce que dit le site lui-même : des vidéos montées de rallyes et de courses de côte.
CORPS = ("Adrille Rallye, ce sont des vidéos montées de rallyes et de courses de côte : WRC, championnat de France, compétitions "
         "nationales et régionales. Elles sont disponibles quelques jours après chaque épreuve, pour que les pilotes et les copilotes "
         "retrouvent leurs propres passages.")


def pub_adrille_rallye() -> str:
    return f"📣 {random.choice(ACCROCHES)}\n{CORPS}\n👉 {SITE}"


def traduire_arabe(texte: str, generateur) -> str:
    """/icham ta phrase : la phrase traduite en arabe, et rien d'autre."""
    texte = texte.strip()
    if not texte:
        return "Format : /icham ta phrase, et je te la renvoie en arabe."
    try:
        return generateur("Tu traduis en arabe (arabe standard moderne, en écriture arabe) la phrase donnée. Réponds uniquement par la traduction, "
                          "sans explication ni translittération.", texte).strip()
    except Exception:
        return "Je n'arrive pas à traduire pour l'instant, réessaie dans un moment."


# --- Duel ----------------------------------------------------------------------------------------------------

def _personnes_citees(session: Session, args: list[str]) -> tuple[list[Personne], list[str]]:
    """Les personnes reconnues dans les arguments, et les mots qui ne désignent personne."""
    trouvees, inconnus = [], []
    for mot in args:
        p = session.scalar(select(Personne).where(func.lower(Personne.prenom) == mot.strip().lower()))
        if p is None:
            inconnus.append(mot)
        elif p not in trouvees:
            trouvees.append(p)
    return trouvees, inconnus


def duel(session: Session, args: list[str], generateur) -> str:
    """/duel [A] [B] : deux noms, un seul (l'adversaire est tiré au sort) ou aucun (deux présents au hasard)."""
    cites, inconnus = _personnes_citees(session, args)
    if inconnus:
        return f"Je ne connais pas « {' '.join(inconnus)} ». Essaie avec des prénoms du groupe."
    if len(cites) > 2:
        return "Un duel, c'est deux personnes : pas plus."
    candidats = defis.presents(session) or list(session.scalars(select(Personne).where(Personne.telegram_id.is_not(None))))
    if len(cites) < 2:
        autres = [p for p in candidats if p not in cites]
        if len(autres) < 2 - len(cites):
            return "Il me faut au moins deux personnes pour un duel."
        cites = cites + random.sample(autres, 2 - len(cites))
    a, b = cites
    gagnant, perdant = random.sample([a, b], 2)  # le tirage est fait ici : le modèle ne choisit pas qui gagne
    try:
        recit = generateur("Tu es Krok, le bot d'un groupe d'amis. Tu commentes un duel avec l'emphase d'un commentateur sportif. Tu inventes une "
                           "discipline absurde, sans aucun rapport avec le ski, la neige ou la montagne. Trois phrases au plus, un emoji au plus. "
                           "Tu n'inventes aucun fait réel sur les personnes et tu ne les rabaisses pas. Le vainqueur est imposé, tu ne le changes pas.",
                           f"Le duel oppose {a.prenom} et {b.prenom}. Vainqueur imposé : {gagnant.prenom}.").strip()
    except Exception:
        recit = ""
    return f"⚔️ {a.prenom} contre {b.prenom}\n" + (recit + "\n" if recit else "") + f"🏆 Vainqueur : {gagnant.prenom}"


# --- Qui est-ce ? ---------------------------------------------------------------------------------------------

def nouvelle_devinette(session: Session) -> tuple[int, str, list[list[tuple[str, str]]]] | str:
    """Une citation tirée au hasard, sans son auteur, avec un bouton par personne et un pour révéler."""
    citation = session.scalar(select(Citation).order_by(func.random()).limit(1))
    if citation is None:
        return "Aucune citation pour l'instant : /citation Adrien \"la phrase\" pour en ajouter."
    devinette = Devinette(citation_id=citation.id)
    session.add(devinette)
    session.commit()
    personnes = list(session.scalars(select(Personne).where(Personne.actif.is_(True)).order_by(Personne.id)))
    boutons = [[(p.prenom, f"q|{devinette.id}|{p.id}")] for p in personnes] + [[("💡 Révéler", f"q|{devinette.id}|r")]]
    return devinette.id, f"🕵️ Qui est-ce ?\n« {citation.texte} »", boutons


def _reponses(session: Session, devinette_id: int) -> list[DevinetteReponse]:
    return list(session.scalars(select(DevinetteReponse).where(DevinetteReponse.devinette_id == devinette_id).order_by(DevinetteReponse.id)))


def repondre(session: Session, devinette_id: int, votant: Personne, choix: str) -> tuple[str, list | None] | None:
    """Enregistre une réponse (sans révéler si elle est juste) ou révèle l'auteur. None si la question n'existe plus ou est close."""
    devinette = session.get(Devinette, devinette_id)
    if devinette is None or devinette.clos:
        return None
    citation = session.get(Citation, devinette.citation_id)
    texte = f"🕵️ Qui est-ce ?\n« {citation.texte} »"
    if choix == "r":
        auteur = session.get(Personne, citation.personne_id)
        bons = [session.get(Personne, r.personne_id).prenom for r in _reponses(session, devinette_id) if r.choix_id == auteur.id]
        devinette.clos = True
        session.commit()
        return texte + f"\n💡 C'était {auteur.prenom}. Bien vu : {', '.join(bons) or 'personne'}.", None
    if session.scalar(select(DevinetteReponse.id).where(DevinetteReponse.devinette_id == devinette_id, DevinetteReponse.personne_id == votant.id)) is None:
        session.add(DevinetteReponse(devinette_id=devinette_id, personne_id=votant.id, choix_id=int(choix)))
        session.commit()
    personnes = list(session.scalars(select(Personne).where(Personne.actif.is_(True)).order_by(Personne.id)))
    boutons = [[(p.prenom, f"q|{devinette_id}|{p.id}")] for p in personnes] + [[("💡 Révéler", f"q|{devinette_id}|r")]]
    ont_repondu = [session.get(Personne, r.personne_id).prenom for r in _reponses(session, devinette_id)]
    return texte + f"\n🗳 Ont répondu : {', '.join(ont_repondu)}", boutons
