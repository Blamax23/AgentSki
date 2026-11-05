"""Les dix défis. Ils ne contraignent jamais le ski : ils se jouent au télésiège, à la pause, au chalet ou en soirée.

Cœur indépendant de Telegram. Le modèle créatif est passé en paramètre (`generateur(systeme, utilisateur) -> texte`).
Mécaniques simples par défaut : tirage au sort, puis validation par vote, note ou réponse ; les auteurs restent anonymes."""
import random
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, personne_par_prenom
from .models import (Archive, ChampJournal, Chute, Defi, DefiVote, Element, Journee, Morceau, Personne, Plat, Presence)
from .rappels import Message

SEPARATEUR = "\n§§\n"  # annonce / réponse cachée d'un quiz


@dataclass
class Annonce:
    texte: str
    boutons: list[list[tuple[str, str]]] = field(default_factory=list)
    media: tuple[str, str] | None = None
    prives: list[tuple[int, Message]] = field(default_factory=list)  # (identifiant Telegram, message privé)
    defi_id: int | None = None


# nom, tirage d'une personne, réserve personnalisable par les participants, disponible
TYPES = {
    "phrase": ("La phrase imposée", True, True, True),
    "chef": ("Le chef du soir", True, True, True),
    "blind": ("Le blind test", False, False, True),
    "quiz": ("Le quiz de la saison", False, True, True),
    "proces": ("Le procès", True, False, True),
    "gage": ("Le gage", True, True, True),
    "debat": ("Le débat du télésiège", False, True, True),
    "pronostic": ("Le pronostic du jour", False, True, True),
    "dj": ("Le DJ", True, False, True),
    "imitation": ("L'imitation", False, False, True),
}
DESCRIPTIONS = {  # ce que fait réellement chaque défi : le briefing n'a le droit de s'appuyer que sur ces phrases
    "phrase": "une personne tirée au sort doit placer une phrase absurde face caméra dans la journée, le groupe valide",
    "chef": "une personne tirée au sort cuisine un plat ce soir, les autres la notent sur 10",
    "quiz": "une question tirée de la réserve ou du journal, chacun répond, la réponse se révèle ensuite",
    "proces": "une personne tirée au sort est accusée à partir d'une de ses chutes ou d'une archive, 30 secondes pour se défendre, le groupe vote",
    "gage": "une personne tirée au sort relève un gage au chalet ou au restaurant, jamais à ski, le groupe valide",
    "debat": "une question absurde à trancher pendant la prochaine montée de télésiège, le verdict est noté au journal",
    "pronostic": "une question sur la journée, chacun répond en privé le matin, le résultat est révélé au récap du soir",
    "blind": "un morceau de la playlist du week-end, chacun devine qui l'a ajouté, la réponse se révèle ensuite",
    "dj": "une personne tirée au sort choisit la musique du trajet ou de la soirée, les autres la notent",
    "imitation": "chacun reçoit en privé un membre du groupe à imiter pendant le repas, les autres devinent",
}
INVENTER = {
    "phrase": "Une phrase absurde et inoffensive qu'un skieur doit placer face caméra dans la journée. Rien d'autre.",
    "chef": "Un plat de montagne ou de chalet, juste son nom. Rien d'autre.",
    "gage": "Un gage drôle et inoffensif à faire au chalet ou au restaurant, jamais pendant une descente et sans gêner la vue ni l'équipement. Une phrase.",
    "debat": "Une question absurde à trancher à plusieurs pendant la montée d'un télésiège. Une phrase.",
    "pronostic": "Une question à réponse oui ou non sur la journée de ski à venir (météo, chutes, retards). Une phrase.",
    "quiz": "Une question de quiz sur le ski en général (pas sur des personnes) avec sa réponse, au format : Question ||| Réponse.",
}
MOTS_INTERDITS_GAGE = ("descente", "en skiant", "en skis", "pendant que tu skie", "les yeux fermés", "lunettes", "masque", "casque",
                       "fixation", "bandeau", "à l'envers sur les skis", "télésiège sans")


# --- Réserve -----------------------------------------------------------------------------------

def types_personnalisables() -> list[tuple[str, str]]:
    return [(cle, nom) for cle, (nom, _, perso, dispo) in TYPES.items() if perso and dispo]


def verifier_gage(texte: str, juge) -> tuple[bool, str]:
    """Refuse un gage qui touche au ski. `juge` répond « OK » ou « REFUS : raison » ; sans juge, une liste de mots sert de secours."""
    try:
        reponse = juge("Tu valides des gages pour un groupe de skieurs. Réponds uniquement « OK », ou « REFUS : » suivi d'une raison courte, si le gage doit "
                       "être fait pendant une descente de ski ou s'il gêne la vue ou l'équipement (casque, lunettes, masque, skis, fixations).", texte)
        if reponse.upper().startswith("REFUS"):
            return False, reponse.split(":", 1)[-1].strip() or "il touche au ski"
        return True, ""
    except Exception:
        if any(m in texte.lower() for m in MOTS_INTERDITS_GAGE):
            return False, "il semble toucher au ski (descente, vue ou équipement)"
        return True, ""


def ajouter_element(session: Session, auteur: Personne, type_: str, texte: str, juge=None) -> str:
    texte = texte.strip()
    if type_ not in dict(types_personnalisables()) or not texte:
        return "Je n'ai pas compris l'élément à ajouter."
    if type_ == "gage":
        ok, raison = verifier_gage(texte, juge) if juge else (True, "")
        if not ok:
            return f"🚫 Gage refusé : {raison}. Les défis ne gênent jamais le ski, propose-en un autre."
    session.add(Element(type=type_, auteur_id=auteur.id, texte=texte, cree_a=_maintenant_aware()))
    session.commit()
    return f"✅ Ajouté à la réserve « {TYPES[type_][0]} ». Ton nom ne sera jamais affiché."


def mes_elements(session: Session, auteur: Personne) -> str:
    elements = list(session.scalars(select(Element).where(Element.auteur_id == auteur.id).order_by(Element.id)))
    if not elements:
        return "Tu n'as rien ajouté. /ajout pour alimenter les réserves."
    return "Tes ajouts (/retirer N pour en supprimer un) :\n" + "\n".join(f"#{e.id} · {TYPES[e.type][0]} : {e.texte}" for e in elements)


def retirer_element(session: Session, auteur: Personne, numero: int) -> str:
    element = session.get(Element, numero)
    if element is None or element.auteur_id != auteur.id:
        return f"Pas d'ajout #{numero} parmi les tiens."
    session.delete(element)
    session.commit()
    return f"🗑 Retiré : {element.texte}"


# --- Tirages ------------------------------------------------------------------------------------

def presents(session: Session) -> list[Personne]:
    """Présents de la journée d'aujourd'hui ; à défaut, toutes les personnes qui ont un compte."""
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
    if journee is not None:
        liste = list(session.scalars(select(Personne).join(Presence, Presence.personne_id == Personne.id).where(
            Presence.journee_id == journee.id).order_by(Personne.id)))
        if liste:
            return liste
    return list(session.scalars(select(Personne).where(Personne.telegram_id.is_not(None)).order_by(Personne.id)))


def tirer_personne(session: Session, candidats: list[Personne]) -> Personne | None:
    """Au hasard, sans retomber deux fois de suite sur la même personne."""
    if not candidats:
        return None
    dernier = session.scalar(select(Defi.tire_id).where(Defi.tire_id.is_not(None)).order_by(Defi.id.desc()).limit(1))
    autres = [p for p in candidats if p.id != dernier]
    return random.choice(autres or candidats)


def tirer_element(session: Session, type_: str, pour: Personne | None, generateur) -> Element:
    """Un élément de la réserve : jamais celui de la personne tirée, mis de côté jusqu'à épuisement, inventé si la réserve est vide."""
    def disponibles():
        requete = select(Element).where(Element.type == type_, Element.utilise.is_(False))
        if pour is not None:
            requete = requete.where((Element.auteur_id != pour.id) | (Element.auteur_id.is_(None)))
        return list(session.scalars(requete))

    dispo = disponibles()
    if not dispo:  # réserve épuisée : tout redevient disponible
        for e in session.scalars(select(Element).where(Element.type == type_, Element.auteur_id.is_not(None))):
            e.utilise = False
        session.commit()
        dispo = disponibles()
    if dispo:
        element = random.choice(dispo)
    else:
        try:
            texte = generateur("Tu inventes un élément pour un jeu entre amis skieurs.", INVENTER[type_]).strip()
        except Exception:
            texte = ""
        element = Element(type=type_, auteur_id=None, texte=texte or "(rien à proposer pour le moment)", cree_a=_maintenant_aware())
        session.add(element)
    element.utilise = True
    session.commit()
    return element


def _derangement(ids: list[int]) -> dict[int, int]:
    """Chacun reçoit une cible différente de lui-même ; plusieurs peuvent avoir la même cible."""
    return {i: random.choice([j for j in ids if j != i]) for i in ids}


# --- Lancer un défi ---------------------------------------------------------------------------------------

def _creer(session: Session, type_: str, tire: Personne | None, contenu: str) -> Defi:
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui()))
    defi = Defi(type=type_, journee_id=journee.id if journee else None, tire_id=tire.id if tire else None, contenu=contenu,
                cree_a=_maintenant_aware())
    session.add(defi)
    session.flush()
    return defi


def _boutons_vote(defi_id: int, pour: str, contre: str) -> list[list[tuple[str, str]]]:
    return [[(pour, f"d|{defi_id}|1"), (contre, f"d|{defi_id}|0")]]


def _boutons_note(defi_id: int) -> list[list[tuple[str, str]]]:
    return [[(str(k), f"d|{defi_id}|{k}") for k in range(1, 6)], [(str(k), f"d|{defi_id}|{k}") for k in range(6, 11)]]


def lancer(session: Session, type_: str, generateur) -> Annonce | str:
    """Tire au sort, enregistre le défi et prépare l'annonce. Renvoie un texte d'explication si le défi ne peut pas se jouer."""
    nom, personne, _, dispo = TYPES[type_]
    if not dispo:
        return f"« {nom} » n'est pas encore disponible : il a besoin de la playlist Spotify, qui n'est pas branchée."
    liste = presents(session)
    if not liste:
        return "Je ne sais pas qui est là : personne n'est présent aujourd'hui."
    tire = tirer_personne(session, liste) if personne else None

    if type_ in ("phrase", "gage"):
        element = tirer_element(session, type_, tire, generateur)
        mission = ("ta mission : placer « {} » face caméra dans la journée" if type_ == "phrase" else "ton gage : {}").format(element.texte)
        texte = f"{'🎙' if type_ == 'phrase' else '😈'} {nom} — {tire.prenom}, {mission}. Le groupe valide."
        defi = _creer(session, type_, tire, texte)
        return Annonce(texte, _boutons_vote(defi.id, "✅ Fait", "❌ Raté"), defi_id=defi.id)

    if type_ == "chef":
        element = tirer_element(session, "chef", tire, generateur)
        session.add(Plat(auteur_id=tire.id, texte=element.texte, cree_a=_maintenant_aware()))
        texte = f"👨‍🍳 {nom} — {tire.prenom}, ce soir tu cuisines : {element.texte}. /course sortira la liste. Les autres notent."
        defi = _creer(session, type_, tire, texte)
        return Annonce(texte, _boutons_note(defi.id), defi_id=defi.id)

    if type_ == "dj":
        texte = f"🎧 {nom} — {tire.prenom} choisit la musique du trajet ou de la soirée. Les autres notent."
        defi = _creer(session, type_, tire, texte)
        return Annonce(texte, _boutons_note(defi.id), defi_id=defi.id)

    if type_ == "proces":
        archive = session.scalar(select(Archive).where(Archive.etape == "ok", Archive.cible_id == tire.id).order_by(func.random()))
        chute = session.scalar(select(Chute).where(Chute.personne_id == tire.id).order_by(func.random()))
        if archive is not None:
            preuve, media = f"pièce à conviction : « {archive.description} »", (archive.type, archive.file_id)
        elif chute is not None:
            jour = session.get(Journee, chute.journee_id).jour
            preuve, media = f"pièce à conviction : une chute le {jour.day:02d}/{jour.month:02d}", None
        else:
            preuve, media = "pièce à conviction : aucune, mais on trouvera", None
        texte = f"⚖️ {nom} — {tire.prenom} est accusé·e ({preuve}). 30 secondes pour se défendre, puis le groupe vote."
        defi = _creer(session, type_, tire, texte)
        return Annonce(texte, _boutons_vote(defi.id, "🔨 Coupable", "🕊 Innocent"), media, defi_id=defi.id)

    if type_ == "debat":
        element = tirer_element(session, "debat", None, generateur)
        texte = f"🪑 {nom} — {element.texte}\nTranchez avant le prochain sommet, puis /verdict suivi de ce qui a été décidé."
        defi = _creer(session, type_, None, texte + SEPARATEUR + element.texte)
        return Annonce(texte, defi_id=defi.id)

    if type_ == "quiz":
        element = tirer_element(session, "quiz", None, generateur)
        question, _, reponse = element.texte.partition("|||")
        if not reponse.strip():
            return "Cette question n'a pas de réponse (format attendu : Question ||| Réponse). Réessaie."
        texte = f"🧠 {nom} — {question.strip()}"
        defi = _creer(session, type_, None, texte + SEPARATEUR + reponse.strip())
        return Annonce(texte, [[("💡 Réponse", f"d|{defi.id}|r"), ("🙋 J'avais bon", f"d|{defi.id}|1")]], defi_id=defi.id)

    if type_ == "blind":
        journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui()))
        morceaux = list(session.scalars(select(Morceau).where(Morceau.weekend_id == journee.weekend_id))) if journee else []
        auteurs = {m.personne_id for m in morceaux}
        if len(morceaux) < 2 or len(auteurs) < 2:
            return "Il faut au moins deux morceaux ajoutés par deux personnes différentes (avec /son) pour jouer au blind test."
        morceau = random.choice(morceaux)
        texte = (f"🎧 {nom} — écoutez ce morceau (lien Spotify) : qui l'a ajouté ?\nhttps://open.spotify.com/track/{morceau.track_id}")
        defi = _creer(session, "blind", None, texte + SEPARATEUR + str(morceau.personne_id))
        candidats = session.scalars(select(Personne).where(Personne.id.in_(auteurs)).order_by(Personne.id))
        boutons = [[(p.prenom, f"d|{defi.id}|{p.id}")] for p in candidats] + [[("💡 Révéler", f"d|{defi.id}|r")]]
        return Annonce(texte, boutons, defi_id=defi.id)

    if type_ == "pronostic":
        element = tirer_element(session, "pronostic", None, generateur)
        texte = f"🔮 {nom} — {element.texte}\nChacun répond en privé, le résultat sera révélé au récap du soir."
        defi = _creer(session, type_, None, texte + SEPARATEUR + element.texte)
        prives = [(p.telegram_id, Message(f"🔮 Pronostic : {element.texte}", _boutons_vote(defi.id, "Oui", "Non")))
                  for p in liste if p.telegram_id]
        return Annonce(texte, prives=prives, defi_id=defi.id)

    # imitation
    if len(liste) < 2:
        return "Il faut au moins deux présents pour une imitation."
    cibles = _derangement([p.id for p in liste])
    defi = _creer(session, "imitation", None, "🎭 L'imitation commence : chacun a reçu sa cible en privé. Imitez-la pendant le repas, les autres devinent !")
    noms = {p.id: p for p in liste}
    for imitateur, cible in cibles.items():
        session.add(DefiVote(defi_id=defi.id, personne_id=imitateur, valeur=cible))
    session.commit()
    prives = [(noms[i].telegram_id, Message(f"🎭 Tu imiteras {noms[c].prenom} pendant le repas, sans te faire repérer."))
              for i, c in cibles.items() if noms[i].telegram_id]
    return Annonce(defi.contenu, prives=prives, defi_id=defi.id)


# --- Validation ----------------------------------------------------------------------------------------------

def _base(defi: Defi) -> str:
    return defi.contenu.split(SEPARATEUR)[0]


def _tally(session: Session, defi: Defi, reponse_vue: bool = False) -> str:
    votes = list(session.scalars(select(DefiVote).where(DefiVote.defi_id == defi.id)))
    if defi.type in ("phrase", "gage"):
        return f"\n✅ {sum(v.valeur == 1 for v in votes)} · ❌ {sum(v.valeur == 0 for v in votes)}"
    if defi.type == "proces":
        return f"\n🔨 Coupable {sum(v.valeur == 1 for v in votes)} · 🕊 Innocent {sum(v.valeur == 0 for v in votes)}"
    if defi.type in ("chef", "dj"):
        return f"\n⭐ {sum(v.valeur for v in votes) / len(votes):.1f}/10 ({len(votes)} note{'s' if len(votes) > 1 else ''})" if votes else "\n⭐ Pas encore de note"
    if defi.type == "quiz":
        texte = f"\n🙋 {sum(v.valeur == 1 for v in votes)} ont trouvé"
        return texte + (f"\n💡 Réponse : {defi.contenu.split(SEPARATEUR)[1]}" if reponse_vue else "")
    return ""


def voter(session: Session, defi_id: int, votant: Personne, valeur: str) -> Message | None:
    """Enregistre un vote ou révèle la réponse d'un quiz. Renvoie le message mis à jour, ou None si le vote est refusé."""
    defi = session.get(Defi, defi_id)
    if defi is None or defi.etat != "en_cours" or defi.type == "imitation":
        return None
    if defi.type == "blind" and valeur == "r":
        auteur = session.get(Personne, int(defi.contenu.split(SEPARATEUR)[1]))
        bons = [session.get(Personne, v.personne_id).prenom for v in session.scalars(select(DefiVote).where(DefiVote.defi_id == defi.id))
                if v.valeur == auteur.id]
        defi.etat = "clos"
        session.commit()
        return Message(f"{_base(defi)}\n💡 C'était le morceau de {auteur.prenom}. Bien vu : {', '.join(bons) or 'personne'}.")
    if defi.type == "quiz" and valeur == "r":
        return Message(_base(defi) + _tally(session, defi, True),
                       [[("🙋 J'avais bon", f"d|{defi.id}|1")]])
    if defi.type in ("phrase", "gage", "proces", "chef", "dj") and defi.tire_id == votant.id:
        return None  # on ne note pas son propre défi
    vote = session.scalar(select(DefiVote).where(DefiVote.defi_id == defi.id, DefiVote.personne_id == votant.id))
    if vote is None:
        session.add(DefiVote(defi_id=defi.id, personne_id=votant.id, valeur=int(valeur)))
    else:
        vote.valeur = int(valeur)
    session.commit()
    if defi.type == "pronostic":
        return Message("🔮 Réponse notée. Résultat au récap du soir.")
    if defi.type == "blind":
        votes = list(session.scalars(select(DefiVote).where(DefiVote.defi_id == defi.id)))
        boutons = [[(session.get(Personne, i).prenom, f"d|{defi.id}|{i}")] for i in sorted({m.personne_id for m in session.scalars(
            select(Morceau).where(Morceau.weekend_id == session.get(Journee, defi.journee_id).weekend_id))})] + [[("💡 Révéler", f"d|{defi.id}|r")]]
        return Message(_base(defi) + f"\n🗳 {len(votes)} réponse{'s' if len(votes) > 1 else ''}", boutons)
    boutons = {"phrase": _boutons_vote(defi.id, "✅ Fait", "❌ Raté"), "gage": _boutons_vote(defi.id, "✅ Fait", "❌ Raté"),
               "proces": _boutons_vote(defi.id, "🔨 Coupable", "🕊 Innocent"), "chef": _boutons_note(defi.id), "dj": _boutons_note(defi.id),
               "quiz": [[("💡 Réponse", f"d|{defi.id}|r"), ("🙋 J'avais bon", f"d|{defi.id}|1")]]}[defi.type]
    return Message(_base(defi) + _tally(session, defi), boutons)


def verdict(session: Session, texte: str) -> str:
    """Note au journal ce qui a été décidé pour le dernier débat du télésiège encore ouvert."""
    defi = session.scalar(select(Defi).where(Defi.type == "debat", Defi.etat == "en_cours").order_by(Defi.id.desc()))
    if defi is None or not texte.strip():
        return "Aucun débat en cours, ou verdict vide. Format : /verdict Oui, la raclette gagne."
    question = defi.contenu.split(SEPARATEUR)[1]
    journee_id = defi.journee_id or session.scalar(select(Journee.id).where(Journee.jour == aujourdhui()))
    if journee_id is None:
        return "Pas de journée du jour pour ranger le verdict."
    session.add(ChampJournal(journee_id=journee_id, cle="debat", valeur=f"{question} → {texte.strip()}", phrase_source="verdict", verifie=True))
    defi.etat = "clos"
    session.commit()
    return f"⚖️ Verdict noté au journal : {texte.strip()}"


def resultat_pronostic(session: Session, oui: bool) -> str:
    defi = session.scalar(select(Defi).where(Defi.type == "pronostic", Defi.etat == "en_cours").order_by(Defi.id.desc()))
    if defi is None:
        return "Aucun pronostic en cours."
    question = defi.contenu.split(SEPARATEUR)[1]
    bons, rates = [], []
    for v in session.scalars(select(DefiVote).where(DefiVote.defi_id == defi.id)):
        (bons if (v.valeur == 1) == oui else rates).append(session.get(Personne, v.personne_id).prenom)
    defi.etat = "clos"
    from .amis import definir_reglage
    definir_reglage(session, f"pronostic_resultat:{defi.id}", "oui" if oui else "non")  # pour le trophée Madame Irma
    session.commit()
    return (f"🔮 Pronostic du jour : {question}\nRéponse : {'oui' if oui else 'non'}.\n"
            f"✅ Juste : {', '.join(bons) or 'personne'}\n❌ Raté : {', '.join(rates) or 'personne'}")


def reveler_imitation(session: Session) -> str:
    defi = session.scalar(select(Defi).where(Defi.type == "imitation", Defi.etat == "en_cours").order_by(Defi.id.desc()))
    if defi is None:
        return "Aucune imitation en cours."
    lignes = [f"• {session.get(Personne, v.personne_id).prenom} imitait {session.get(Personne, v.valeur).prenom}"
              for v in session.scalars(select(DefiVote).where(DefiVote.defi_id == defi.id))]
    defi.etat = "clos"
    session.commit()
    return "🎭 Qui imitait qui :\n" + "\n".join(lignes)
