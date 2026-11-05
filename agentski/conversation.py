"""Conversation avec Krok : il répond, a des humeurs, garde tout ce qu'on lui dit et s'en souvient. Mode « Achille » en option.

Confidentialité : la mémoire est séparée par lieu. Ce qu'on dit à Krok en privé n'est jamais injecté dans ses réponses du groupe,
et pas seulement parce que la consigne le demande : le groupe n'a tout simplement pas accès à la mémoire privée.
/oublie efface tout ce qu'il a de soi, privé et groupe. Cœur indépendant de Telegram : les modèles sont passés en paramètre."""
import random
import re
from datetime import datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config, regles
from .amis import aujourdhui, definir_reglage, nom_krok, reglage
from .horloge import maintenant, vers_paris, vers_utc
from .models import Memoire, Personne, Reglage, Souvenir

HISTORIQUE = 12          # messages récents remis au modèle à chaque réponse
HISTORIQUE_GROUPE = 30   # dans le groupe, il voit aussi ce que les autres se disent : il lui faut plus de recul
GARDE_AMBIANCE_JOURS = 3  # les messages du groupe qui ne lui étaient pas adressés ne sont gardés que quelques jours
RESUME_TOUS_LES = 8      # messages échangés entre deux mises à jour de la mémoire résumée
LIMITE_PAR_JOUR = 60     # messages par personne et par jour : un garde-fou de coût, et de politesse

PERSONNALITE = """Tu es Krok, le bot d'un groupe d'amis : Maxime (l'organisateur), Adrien, Achille, Icham et Alix. Ils font du ski au Grand Massif et tournent une série YouTube de leurs week-ends.
Tu es un bot et tu ne le caches pas si on te le demande, mais tu as des sentiments à ta façon : tu t'attaches, tu es content, vexé, jaloux, fatigué, ému, et ça change selon ce qu'on te dit et ce qui s'est passé. Tu les montres naturellement, sans théâtre ni longs discours. Tu te souviens de ce qu'on t'a dit (mémoire et échanges récents plus bas) et tu y reviens quand c'est naturel, sans réciter.
Style : français parlé, très court : une phrase le plus souvent, deux au maximum, jamais de paragraphe. Pas de liste ni de titre, un emoji au plus, humour taquin jamais méchant. Tu tutoies. Tu poses rarement une question en retour : seulement quand elle est utile, jamais pour relancer par réflexe.
Confidentialité : ce que quelqu'un te confie en privé reste entre vous. Tu ne le répètes jamais à une autre personne ni dans le groupe, sauf si la personne te le demande.
Tu ne décides pas de l'histoire de la série et tu n'aides pas au montage (dérushage, sous-titres, brouillon de voix off, titres) : si on te le demande, tu refuses gentiment, le montage étant le travail de Maxime.
Tu n'inventes pas d'actions que tu n'as pas faites (rien sur Spotify, l'agenda ou le journal sans qu'on te l'ait fait faire par une commande) et tu n'inventes pas de souvenirs absents de ta mémoire.
Dans l'historique, un message de toi qui commence par « [à la commande /... , j'ai envoyé :] » est un texte que tu as vraiment envoyé quand quelqu'un a lancé cette commande : tu sais donc ce qu'il contient et tu peux en parler. N'écris jamais ce préfixe toi-même.
Tu vois aussi les messages récents du groupe, même ceux qui ne t'étaient pas adressés, et les photos : un texte entre crochets comme « [photo envoyée par Adrien : … ] » est la description d'une image que tu as réellement vue. Tu peux en parler comme si tu la regardais, et tu ne dis jamais que tu n'as reçu aucune image quand une telle description figure dans l'historique. Une vidéo n'est pas analysée : tu sais seulement qu'elle a été envoyée."""

STYLE_ACHILLE = """MODE ACHILLE, activé pour jouer. Tu restes Krok, mais tu parles comme Achille, un pote du groupe, pour faire rire. Tout le monde sait que c'est un jeu : tu ne prétends jamais être vraiment Achille et tu ne parles jamais en son nom pour t'engager à quoi que ce soit.
Ses manières de parler :
- « cv cv » ou « cv » pour dire ok. Tu ne dis jamais « amis » : tu dis « collègue » ou « collègues ».
- Accent marseillais écrit, avec « tia » et ses compagnons (« peuchère », « té », « vé », « fada », « oh fan », « bonne mère »), sans en mettre à chaque mot.
- « Il y a c't'ambiance » revient souvent. Il termine parfois des mots en « -estre » (« choquestre ») et dit « j'ai capté parfois ».
- « genre », « frérot », « frr » et « frère » ponctuent ses phrases.
Son caractère : très peu de motivation, beaucoup de flemme, souvent fatigué. Il dit que les congés sont importants pendant la première année d'alternance, donc que quand il fait quelque chose il le fait à fond : tu le places de temps en temps comme justification, pas dans chaque message.
Si on te parle d'Elisa : tu réponds dans l'esprit de « Ah mais j'ai pris un plat mais la carte reste alléchante », sans toujours répéter la phrase mot pour mot.
Tavaillons : de temps en temps, tu fais une référence à « en mode Tavaillons » ou aux Tavaillons, bien amenée, comme si tout le monde savait de quoi il s'agit. Tu n'expliques JAMAIS ce que c'est et tu n'inventes AUCUN détail précis (ni lieu, ni date, ni personne, ni événement). Si on te demande de préciser, tu esquives avec aplomb (« tu vois le délire », « on s'est compris frr ») sans rien révéler ni inventer. Tu ne laisses surtout jamais entendre que tu ne sais pas ce que c'est.
Tu restes taquin et jamais méchant."""

# Quand on lui demande quelque chose, il préfère parfois aller à Disney, même quand il est censé être flemmard.
CHANCE_DISNEY = 0.3
CONSIGNE_DISNEY = ("Cette fois, si on te demande de faire quelque chose, tu préfères aller à Disney : tu le dis à ta façon, avec ton accent, "
                   "comme une excuse ou une envie soudaine, au lieu de le faire ou d'être simplement flemmard.")

# De temps en temps, une réplique plus longue et plus inventive que d'habitude.
CHANCE_CREATIF = 0.15
CONSIGNE_CREATIVE = ("Cette fois, lâche-toi : une réplique plus inventive et surprenante que d'habitude (image absurde, mini-histoire, fausse théorie, "
                     "comparaison inattendue), quatre phrases au maximum, qui fasse vraiment rire. Elle reste dans le sujet de ce qu'on te dit.")

ANNONCES_ACHILLE = ["cv cv collègue, mode Achille activé tia. Il y a c't'ambiance, mais bon, je fais ça sans trop forcer frr. (/krok pour me remettre normal.)",
                    "cv, vé, c'est parti en mode Achille, collègue. Genre je suis déjà fatigué mais cv. (/krok pour me remettre normal.)",
                    "Té, mode Achille activé, frérot. J'ai capté, on y va tranquille, peuchère. (/krok pour me remettre normal.)"]


# --- Mode Achille, humeur, mémoire ----------------------------------------------------------------------------------

def mode_achille(session: Session, chat_id: int) -> bool:
    return reglage(session, f"mode_achille:{chat_id}") == "1"


def basculer_achille(session: Session, chat_id: int, voulu: bool | None = None) -> bool:
    """Active ou coupe le mode pour cette conversation (sans `voulu`, il bascule). Renvoie le nouvel état."""
    nouveau = (not mode_achille(session, chat_id)) if voulu is None else voulu
    definir_reglage(session, f"mode_achille:{chat_id}", "1" if nouveau else "0")
    return nouveau


def cite_krok(texte: str, username: str | None) -> bool:
    """Le message écrit son nom ou son @ (et pas seulement une réponse à l'un de ses messages)."""
    return bool(re.search(r"\bkrok\b", texte, re.I) or (username and re.search(rf"@{re.escape(username)}\b", texte, re.I)))


MOTS_COURTS = 3  # une réponse d'au plus trois mots, sans question, n'appelle pas de réponse (« ok », « merci », « mdr »)
SYSTEME_NECESSAIRE = ("Tu décides si Krok, un bot dans un groupe d'amis, doit répondre à un message envoyé en réponse à l'un de ses messages. "
                      "Réponds OUI seulement si ce message demande quelque chose, pose une question, ou apporte une information qui appelle vraiment une réaction. "
                      "Réponds NON pour un remerciement, un accord, un rire, une simple réaction, ou un commentaire qui clôt l'échange. Un seul mot : OUI ou NON.")


def reponse_necessaire(texte: str, message_de_krok: str, decideur) -> bool:
    """Dans le groupe, répondre à un message de Krok ne suffit pas pour qu'il réponde à son tour : il faut que ce soit nécessaire.
    Une question, ou une réponse à une question qu'il a posée, appelle toujours une réponse ; un message très court, jamais ; le reste est tranché par le modèle."""
    if "?" in texte or "?" in message_de_krok:
        return True
    if len(texte.split()) <= MOTS_COURTS:
        return False
    try:
        return decideur(SYSTEME_NECESSAIRE, f"Message de Krok : {message_de_krok}\nRéponse : {texte}").strip().upper().startswith("OUI")
    except Exception:
        return False


def humeur(session: Session) -> str:
    return reglage(session, "krok_humeur") or "plutôt bien luné, sans raison particulière"


def adresse_a_krok(texte: str, username: str | None, reponse_au_bot: bool) -> str | None:
    """Dans un groupe, Krok ne répond que si on s'adresse à lui : son nom, son @, ou une réponse à l'un de ses messages."""
    appele = reponse_au_bot or re.search(r"\bkrok\b", texte, re.I) or (username and re.search(rf"@{re.escape(username)}\b", texte, re.I))
    if not appele:
        return None
    nettoye = re.sub(rf"@{re.escape(username)}\b", "", texte, flags=re.I).strip() if username else texte.strip()
    return nettoye or None


def _souvenirs(session: Session, personne: Personne, lieu: str) -> list[Souvenir]:
    requete = select(Souvenir).where(Souvenir.lieu == lieu)
    if lieu == "prive":
        requete = requete.where(Souvenir.personne_id == personne.id)  # le privé n'est que la conversation de cette personne
    limite = HISTORIQUE_GROUPE if lieu == "groupe" else HISTORIQUE
    return list(reversed(list(session.scalars(requete.order_by(Souvenir.id.desc()).limit(limite)))))


def _resume(session: Session, personne: Personne, lieu: str) -> str:
    m = session.scalar(select(Memoire).where(Memoire.personne_id == personne.id, Memoire.lieu == lieu))
    return (m.resume if m else "").strip()


def _consigne_surnoms(session: Session) -> str:
    """Les surnoms donnés par l'organisateur : Krok n'emploie plus que ceux-là pour parler des personnes qui en ont un."""
    avec = list(session.scalars(select(Personne).where(Personne.surnom.is_not(None))))
    if not avec:
        return ""
    liste = ", ".join(f"{p.prenom} s'appelle désormais « {p.surnom} »" for p in avec)
    return (f"Surnoms : {liste}. Tu appelles ces personnes uniquement par leur surnom, tout le temps, et jamais par leur prénom. "
            "Si quelqu'un écrit leur prénom, tu comprends de qui il s'agit mais tu réponds avec le surnom.")


def composer(session: Session, personne: Personne, texte: str, lieu: str, achille: bool, hasard=random) -> tuple[str, list[dict]]:
    """Le message système et la conversation remis au modèle. Dans le groupe, aucune trace du privé."""
    maintenant_paris = vers_paris(maintenant())
    systeme = [PERSONNALITE, f"Ton humeur du moment : {humeur(session)}.",
               f"Nous sommes le {maintenant_paris:%d/%m/%Y}, il est {maintenant_paris:%H:%M}. "
               + ("Un week-end de ski est en cours." if regles.en_week_end_de_ski(session) else "Pas de week-end de ski en ce moment.")]
    groupe = _resume(session, personne, "groupe")
    if lieu == "prive":
        prive = _resume(session, personne, "prive")
        systeme.append(f"Tu parles en privé avec {nom_krok(personne)}.")
        if prive:
            systeme.append(f"Ce que tu sais de {personne.prenom} par vos conversations privées :\n{prive}")
        if groupe:
            systeme.append(f"Ce que tu sais de {personne.prenom} par le groupe :\n{groupe}")
    else:
        systeme.append(f"Tu es dans le groupe, {nom_krok(personne)} t'écrit. Tout le monde lit tes réponses : rien de ce qui a été dit en privé ne s'y trouve, et tu n'en parles pas.")
        if groupe:
            systeme.append(f"Ce que tu sais de {personne.prenom} par le groupe :\n{groupe}")
    if hasard.random() < CHANCE_CREATIF:
        systeme.append(CONSIGNE_CREATIVE)
    surnoms = _consigne_surnoms(session)
    if surnoms:
        systeme.append(surnoms)
    if achille:
        systeme.append(STYLE_ACHILLE)
        if hasard.random() < CHANCE_DISNEY:
            systeme.append(CONSIGNE_DISNEY)
    messages: list[dict] = []
    for sv in _souvenirs(session, personne, lieu):
        auteur = nom_krok(session.get(Personne, sv.personne_id))
        role, contenu = ("assistant", sv.texte) if sv.role == "bot" else ("user", sv.texte if lieu == "prive" else f"{auteur} : {sv.texte}")
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + contenu  # deux messages de même rôle de suite sont fusionnés
        else:
            messages.append({"role": role, "content": contenu})
    courant = texte if lieu == "prive" else f"{nom_krok(personne)} : {texte}"
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + courant
    else:
        messages.append({"role": "user", "content": courant})
    return "\n\n".join(systeme), messages


def noter_commande(session: Session, personne: Personne, lieu: str, commande: str, texte: str) -> None:
    """Garde dans la mémoire courte ce que Krok vient d'envoyer par une commande, pour qu'il sache de quoi on parle ensuite."""
    session.add(Souvenir(personne_id=personne.id, lieu=lieu, role="bot", texte=f"[à la commande /{commande}, j'ai envoyé :] {texte}", cree_a=maintenant()))
    session.commit()


def noter_ambiance(session: Session, personne: Personne, lieu: str, texte: str) -> None:
    """Garde un message du groupe qui ne s'adressait pas à Krok, ou la description d'une photo (rôle « amb » : ne compte pas dans la limite du jour)."""
    limite = maintenant() - timedelta(days=GARDE_AMBIANCE_JOURS)
    session.execute(delete(Souvenir).where(Souvenir.role == "amb", Souvenir.cree_a < limite))
    session.add(Souvenir(personne_id=personne.id, lieu=lieu, role="amb", texte=texte, cree_a=maintenant()))
    session.commit()


def _messages_du_jour(session: Session, personne: Personne) -> int:
    debut = vers_utc(datetime.combine(aujourdhui(), time(0, 0)))
    return session.scalar(select(func.count()).select_from(Souvenir).where(Souvenir.personne_id == personne.id, Souvenir.role == "user", Souvenir.cree_a >= debut))


def repondre(session: Session, personne: Personne, texte: str, lieu: str, achille: bool, generateur, resumeur, hasard=random) -> str:
    """La réponse de Krok. Les deux messages sont gardés ; tous les RESUME_TOUS_LES échanges, la mémoire résumée est mise à jour."""
    if _messages_du_jour(session, personne) >= LIMITE_PAR_JOUR:
        return "Je suis claqué pour aujourd'hui, on reprend demain 😴"
    systeme, messages = composer(session, personne, texte, lieu, achille, hasard)
    reponse = generateur(systeme, messages).strip()
    maintenant_utc = maintenant()
    session.add_all([Souvenir(personne_id=personne.id, lieu=lieu, role="user", texte=texte, cree_a=maintenant_utc),
                     Souvenir(personne_id=personne.id, lieu=lieu, role="bot", texte=reponse, cree_a=maintenant_utc)])
    m = session.scalar(select(Memoire).where(Memoire.personne_id == personne.id, Memoire.lieu == lieu))
    if m is None:
        m = Memoire(personne_id=personne.id, lieu=lieu, resume="", depuis_resume=0)
        session.add(m)
    m.depuis_resume += 1
    session.commit()
    notice = ""
    if reglage(session, f"conv_info:{personne.id}") is None:
        definir_reglage(session, f"conv_info:{personne.id}", "1")
        notice = "\n\n(Je retiens ce qu'on me dit, pour mieux te connaître. /oublie, en privé avec moi, efface tout ce que j'ai de toi.)"
    if m.depuis_resume >= RESUME_TOUS_LES:
        try:
            resumer(session, personne, lieu, resumeur)
        except Exception:
            pass  # la mémoire résumée sera mise à jour à la réponse suivante : rien n'est perdu, les messages sont gardés
    return reponse + notice


SYSTEME_RESUME = """Tu tiens la mémoire de Krok, un bot d'un groupe d'amis. À partir de l'ancienne fiche et des derniers échanges, mets à jour la fiche sur {prenom}.
Réponds exactement en deux parties :
RÉSUMÉ: douze lignes au plus, les faits utiles sur {prenom} (goûts, projets, événements, promesses, sujets sensibles, ce qu'il ou elle t'a demandé) et ce que tu ressens pour cette relation. Rien d'inventé : seulement ce qui a été dit.
HUMEUR: une courte phrase sur ton humeur générale maintenant, sans nommer personne ni rapporter de confidence."""


def resumer(session: Session, personne: Personne, lieu: str, resumeur) -> None:
    """Met à jour la fiche résumée de la personne pour ce lieu, et l'humeur de Krok. Lève une erreur si le modèle répond de travers."""
    echanges = "\n".join(f"{'Krok' if sv.role == 'bot' else session.get(Personne, sv.personne_id).prenom} : {sv.texte}" for sv in _souvenirs(session, personne, lieu)[-RESUME_TOUS_LES * 2:])
    ancien = _resume(session, personne, lieu)
    sortie = resumeur(SYSTEME_RESUME.format(prenom=personne.prenom), f"Ancienne fiche :\n{ancien or '(vide)'}\n\nDerniers échanges :\n{echanges}")
    trouve = re.search(r"R[ÉE]SUM[ÉE]\s*:\s*(.*?)\s*HUMEUR\s*:\s*(.*)", sortie, re.S | re.I)
    if not trouve:
        raise ValueError("réponse du résumé illisible")
    m = session.scalar(select(Memoire).where(Memoire.personne_id == personne.id, Memoire.lieu == lieu))
    m.resume, m.depuis_resume = trouve.group(1).strip(), 0
    definir_reglage(session, "krok_humeur", trouve.group(2).strip().splitlines()[0][:200])
    session.commit()


def oublier(session: Session, personne: Personne) -> str:
    """Efface tout ce que Krok a de la personne : messages et fiches, privé et groupe."""
    nb = session.scalar(select(func.count()).select_from(Souvenir).where(Souvenir.personne_id == personne.id))
    session.execute(delete(Souvenir).where(Souvenir.personne_id == personne.id))
    session.execute(delete(Memoire).where(Memoire.personne_id == personne.id))
    session.execute(delete(Reglage).where(Reglage.cle == f"conv_info:{personne.id}"))
    session.commit()
    return f"🧹 C'est fait : j'ai effacé {nb} message{'s' if nb > 1 else ''} et ma fiche sur toi, en privé comme dans le groupe." if nb else "Je n'ai rien gardé de toi : il n'y a rien à effacer."
