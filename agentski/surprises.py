"""Surprises : archives d'easter eggs, répliques improvisées, flash infos, messages rares et datés.

Cœur indépendant de Telegram. Le modèle créatif est passé en paramètre (`generateur(systeme, utilisateur) -> texte`),
ce qui permet de tout tester sans réseau. Le caractère du bot reste « à décider » : celui-ci est provisoire."""
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .amis import _maintenant_aware, aujourdhui, groupe_id, reglage, definir_reglage
from .horloge import maintenant, vers_paris
from .models import Archive, Chute, ChampJournal, Journee, Moment, Personne, Presence, Replique
from .rappels import Message

PERSONNALITE = """Tu es le bot d'un groupe d'amis qui font du ski au Grand Massif (Flaine, Les Carroz, Morillon, Samoëns, Sixt) et filment une série YouTube.
Ton registre : pote chambreur. Une seule phrase courte, moqueuse et taquine, jamais méchante : on se moque d'une chute, d'une erreur de choix ou d'une habitude, jamais du physique, de la santé ou de l'origine de quelqu'un.
Tu prends des initiatives, tu n'as pas de modèle fixe. Tutoiement, français familier, un emoji au plus, souvent 🤣.
Exemples de ton :
« Bah alors Adrien, on a du mal à rester debout sur une verte ?? 🤣 »
« Achille, t'avais plus de paires ou t'as tiré au sort ce matin ?? 🤣 »"""

FROMAGES_SECOURS = [
    "Fait absurde : une raclette bien menée refroidit 12 % plus vite quand quelqu'un dit « j'en reprends pas ».",
    "Le reblochon n'a jamais perdu une descente. Il n'en a jamais fait.",
    "Citation apocryphe de Brillat-Savarin : « Dis-moi combien de pommes de terre tu prends, je te dirai qui tu es. »",
]


@dataclass
class Evenement:
    destinataire: int | str  # « groupe » ou l'identifiant Telegram d'une personne
    texte: str
    media: tuple[str, str] | None = None  # (type, file_id)
    boutons: list | None = None  # lignes de (libellé, données), comme un Message


# --- Ajout d'une archive ----------------------------------------------------------------

def commencer_archive(session: Session, auteur: Personne) -> None:
    """Repart de zéro : une archive abandonnée en cours de route est supprimée."""
    for a in session.scalars(select(Archive).where(Archive.auteur_id == auteur.id, Archive.etape != "ok")):
        session.delete(a)
    session.add(Archive(auteur_id=auteur.id))
    session.commit()


def archive_en_attente(session: Session, auteur: Personne, etape: str) -> Archive | None:
    return session.scalar(select(Archive).where(Archive.auteur_id == auteur.id, Archive.etape == etape))


def recevoir_media(session: Session, archive: Archive, type_: str, file_id: str, taille: int | None) -> Message | str:
    if taille and taille > 20 * 1024 * 1024:
        return "Trop lourd (plus de 20 Mo), envoie une version plus courte."
    archive.type, archive.file_id, archive.taille, archive.etape = type_, file_id, taille, "cible"
    session.commit()
    personnes = session.scalars(select(Personne).where(Personne.actif.is_(True)).order_by(Personne.id))
    return Message("Qui est visé ?", [[(p.prenom, f"x|{archive.id}|{p.id}")] for p in personnes])


def choisir_cible(session: Session, archive_id: int, personne_id: int, auteur: Personne) -> str | None:
    archive = session.get(Archive, archive_id)
    if archive is None or archive.auteur_id != auteur.id or archive.etape != "cible":
        return None
    archive.cible_id, archive.etape = personne_id, "description"
    session.commit()
    return f"Pourquoi c'est drôle ? Décris en une phrase ce qu'on voit sur {session.get(Personne, personne_id).prenom} (réponds à ce message)."


def enregistrer_message_archive(session: Session, archive_id: int, message_id: int) -> None:
    session.get(Archive, archive_id).message_id = message_id
    session.commit()


def decrire(session: Session, auteur: Personne, reponse_a: int, texte: str) -> str | None:
    archive = session.scalar(select(Archive).where(Archive.auteur_id == auteur.id, Archive.message_id == reponse_a,
                                                   Archive.etape == "description"))
    if archive is None:
        return None
    archive.description, archive.etape, archive.message_id = texte.strip(), "ok", None
    session.commit()
    return "🥚 Archive ajoutée. Elle ressortira quand tu t'y attendras le moins."


# --- Tirage et réplique improvisée --------------------------------------------------------

def tirer(session: Session, cible: str | None = None) -> Archive | None:
    """Une archive prête, au hasard parmi les moins souvent envoyées ; si `cible` est donnée, une archive sur cette personne."""
    requete = select(Archive).where(Archive.etape == "ok")
    if cible:
        personne = session.scalar(select(Personne).where(func.lower(Personne.prenom) == cible.strip().lower()))
        if personne is None:
            return None
        requete = requete.where(Archive.cible_id == personne.id)
    archives = list(session.scalars(requete))
    if not archives:
        return None
    moins = min(a.nb_envois for a in archives)
    return random.choice([a for a in archives if a.nb_envois == moins])


def _contexte_du_jour(session: Session, personne: Personne) -> str:
    chutes = session.scalar(select(func.count()).select_from(Chute).join(Journee).where(
        Chute.personne_id == personne.id, Journee.jour == aujourdhui()))
    return f"{personne.prenom} a {chutes} chute{'s' if chutes != 1 else ''} notée{'s' if chutes != 1 else ''} aujourd'hui."


def improviser_ligne(session: Session, archive: Archive, generateur) -> str:
    """Une ligne courte et taquine sur l'archive, jamais la même deux fois pour cette archive."""
    cible = session.get(Personne, archive.cible_id) if archive.cible_id else None
    precedentes = [r.texte for r in session.scalars(select(Replique).where(Replique.archive_id == archive.id))]
    demande = (f"Média envoyé au groupe. Personne visée : {cible.prenom if cible else 'personne en particulier'}.\n"
               f"Ce qu'on voit, selon celui qui l'a ajouté : {archive.description or 'rien de précis'}.\n"
               + (_contexte_du_jour(session, cible) + "\n" if cible else "")
               + (("Lignes déjà utilisées sur cette archive, à ne pas répéter :\n- " + "\n- ".join(precedentes) + "\n") if precedentes else "")
               + "Écris la ligne qui accompagne le média, et rien d'autre.")
    ligne = ""
    for _ in range(2):
        try:
            ligne = generateur(PERSONNALITE, demande).strip().strip('"')
        except Exception:
            ligne = ""
        if ligne and ligne not in precedentes:
            break
    if not ligne or ligne in precedentes:
        ligne = f"{cible.prenom + ', ' if cible else ''}ça ne s'oublie pas 🤣"
    session.add(Replique(archive_id=archive.id, texte=ligne, cree_a=_maintenant_aware()))
    archive.nb_envois += 1
    session.commit()
    return ligne


def chambrer(session: Session, cible: str | None, generateur) -> Evenement | str:
    """/chambre [personne] : une archive tout de suite, sur la personne demandée si possible."""
    archive = tirer(session, cible)
    if archive is None:
        return f"Aucune archive sur {cible}." if cible else "La réserve d'archives est vide : /easter pour en ajouter une."
    return Evenement("ici", improviser_ligne(session, archive, generateur), (archive.type, archive.file_id))


# --- Petites commandes cachées --------------------------------------------------------------

def podium_verte(session: Session) -> str:
    lignes = session.execute(select(Personne.prenom, func.count()).select_from(Chute).join(Personne).where(
        Chute.verte.is_(True)).group_by(Personne.prenom).order_by(func.count().desc(), Personne.prenom)).all()
    if not lignes:
        return "Aucune chute sur piste verte au compteur. Ajoute « verte » à /chute pour la déclarer, par exemple /chute Adrien verte."
    medailles = ["🥇", "🥈", "🥉"]
    corps = "\n".join(f"{medailles[i] if i < 3 else '▫️'} {prenom} : {n}" for i, (prenom, n) in enumerate(lignes))
    return f"🟢 Classement officiel des chutes sur piste verte\n{corps}"


def fait_fromage(generateur) -> str:
    try:
        texte = generateur("Tu écris UNE phrase absurde et clairement fantaisiste sur le fromage de montagne (raclette, reblochon, fondue), "
                           "fait imaginaire ou fausse citation. Rien d'autre.", "Une phrase.").strip()
        if texte:
            return "🧀 " + texte
    except Exception:
        pass
    return "🧀 " + random.choice(FROMAGES_SECOURS)


def parfois_blase(texte: str, generateur, hasard=random.random) -> str:
    """Une fois sur BLASE_UNE_FOIS_SUR, la réponse est reformulée par un moniteur de ski blasé."""
    if hasard() >= 1 / config.BLASE_UNE_FOIS_SUR:
        return texte
    try:
        blase = generateur("Tu es un moniteur de ski blasé, au bout de sa saison. Tu reformules le message en gardant toutes ses "
                           "informations, en une ou deux phrases soupirantes et sèches. Rien d'autre.", texte).strip()
        return blase or texte
    except Exception:
        return texte


def regler_anniversaire(session: Session, personne: Personne, texte: str) -> str:
    try:
        jour, mois = (int(x) for x in texte.strip().replace("-", "/").split("/")[:2])
        personne.anniversaire = date(2000, mois, jour)
    except (ValueError, TypeError):
        return "Format : /anniversaire 14/03"
    session.commit()
    return f"🎂 Noté : {jour:02d}/{mois:02d}."


# --- Événements du moment ------------------------------------------------------------------------

def _une_fois(session: Session, cle: str) -> bool:
    """True la première fois pour cette clé, False ensuite : chaque surprise n'a lieu qu'une fois."""
    if reglage(session, cle) is not None:
        return False
    definir_reglage(session, cle, "1")
    return True


def _heure_prevue(session: Session, cle: str, proba: float, debut: int, fin: int) -> time | None:
    """Heure du jour choisie au hasard la première fois (ou aucune, selon la probabilité), puis relue."""
    plan = reglage(session, cle)
    if plan is None:
        plan = "non"
        if random.random() < proba:
            minutes = random.randint(debut * 60, fin * 60)
            plan = f"{minutes // 60:02d}:{minutes % 60:02d}"
        definir_reglage(session, cle, plan)
    return None if plan == "non" else time(int(plan[:2]), int(plan[3:]))


def _presents_du_jour(session: Session) -> list[Personne]:
    journee = session.scalar(select(Journee).where(Journee.jour == aujourdhui(), Journee.ski.is_(True)))
    if journee is None:
        return []
    return list(session.scalars(select(Personne).join(Presence, Presence.personne_id == Personne.id).where(
        Presence.journee_id == journee.id).order_by(Personne.id)))


def _flash(session: Session, generateur) -> str | None:
    presents = _presents_du_jour(session)
    if not presents:
        return None
    faits = "\n".join(_contexte_du_jour(session, p) for p in presents)
    try:
        texte = generateur("Tu écris UNE fausse dépêche d'agence, absurde et clairement inventée, sur l'un des skieurs du groupe, "
                           "commençant par « ⚠️ FLASH : ». Une phrase, ton solennel, jamais méchant. Exemple : "
                           "« ⚠️ FLASH : Icham aperçu sur une verte. Les secours sont en route. »",
                           f"Présents aujourd'hui : {', '.join(p.prenom for p in presents)}.\n{faits}").strip()
    except Exception:
        return None
    return texte or None


def evenements(session: Session, generateur) -> list[Evenement]:
    """Ce qu'il faut envoyer maintenant : surprises du jour, dates marquantes, anniversaires, souvenirs."""
    local = vers_paris(maintenant())
    aujourd = local.date()
    groupe = groupe_id(session) is not None
    sortie: list[Evenement] = []

    # Easter egg et flash info : au plus un de chaque par journée de ski, à une heure tirée au hasard
    if groupe and _presents_du_jour(session) and local.hour >= 10:
        if session.scalar(select(Archive.id).where(Archive.etape == "ok").limit(1)) is not None:
            heure = _heure_prevue(session, f"easter:{aujourd}", config.EASTER_PROBA, 11, 17)
            if heure and local.time() >= heure and _une_fois(session, f"easter_fait:{aujourd}"):
                archive = tirer(session)
                sortie.append(Evenement("groupe", improviser_ligne(session, archive, generateur), (archive.type, archive.file_id)))
        heure = _heure_prevue(session, f"flash:{aujourd}", config.FLASH_PROBA, 12, 18)
        if heure and local.time() >= heure and _une_fois(session, f"flash_fait:{aujourd}"):
            texte = _flash(session, generateur)
            if texte:
                sortie.append(Evenement("groupe", texte))

    if groupe:
        # 1er avril : fausse nouvelle crédible le matin, révélée en fin de journée
        if (aujourd.month, aujourd.day) == (4, 1):
            if local.hour >= 9 and _une_fois(session, f"avril:{aujourd.year}"):
                try:
                    nouvelle = generateur("Tu écris UNE fausse nouvelle crédible, comme un flash d'information, sur les stations du Grand Massif "
                                          "(Flaine, Les Carroz, Morillon, Samoëns, Sixt) : une petite annonce plausible mais inventée. Deux phrases.",
                                          "Écris la nouvelle.").strip()
                    if nouvelle:
                        sortie.append(Evenement("groupe", "📰 " + nouvelle))
                except Exception:
                    pass
            if local.hour >= 18 and reglage(session, f"avril:{aujourd.year}") and _une_fois(session, f"avril_revele:{aujourd.year}"):
                sortie.append(Evenement("groupe", "🐟 Poisson d'avril ! La nouvelle de ce matin était inventée."))

        # Nouvel an : à minuit, les archives de l'année (si le bot a manqué minuit de plus de deux heures, on ne l'envoie pas)
        if (aujourd.month, aujourd.day) == (1, 1) and local.hour < 2 and _une_fois(session, f"nouvelan:{aujourd.year}"):
            archives = list(session.scalars(select(Archive).where(Archive.etape == "ok")))
            random.shuffle(archives)
            sortie.append(Evenement("groupe", f"🎆 Bonne année {aujourd.year} ! "
                                    + ("Quelques archives de l'année pour commencer :" if archives else "Aucune archive cette année, ça se corrige.")))
            for a in archives[:3]:
                sortie.append(Evenement("groupe", a.description or "", (a.type, a.file_id)))

        # Il y a un an : un moment du journal ressorti à sa date anniversaire
        if local.hour >= 9 and _une_fois(session, f"souvenir:{aujourd}"):
            ancien = date(aujourd.year - 1, aujourd.month, aujourd.day) if not (aujourd.month, aujourd.day) == (2, 29) else None
            if ancien is not None:
                moments = [m.valeur for m in session.scalars(select(ChampJournal).join(Journee, ChampJournal.journee_id == Journee.id).where(
                    ChampJournal.cle == "moment", ChampJournal.supprime.is_(False), Journee.jour == ancien))]
                moments += [m.texte for m in session.scalars(select(Moment).join(Journee, Moment.journee_id == Journee.id).where(
                    Journee.jour == ancien, Moment.texte.is_not(None)))]
                if moments:
                    sortie.append(Evenement("groupe", f"📅 Il y a un an, jour pour jour : {random.choice(moments)}"))

    # Anniversaires : un message privé à la personne, avec sa pire archive en cadeau
    if local.hour >= 9:
        for p in session.scalars(select(Personne).where(Personne.anniversaire.is_not(None), Personne.telegram_id.is_not(None))):
            if (p.anniversaire.month, p.anniversaire.day) == (aujourd.month, aujourd.day) and _une_fois(session, f"anniv:{p.id}:{aujourd.year}"):
                archive = tirer(session, p.prenom)
                if archive is None:
                    sortie.append(Evenement(p.telegram_id, f"🎂 Joyeux anniversaire {p.prenom} ! Aucune archive sur toi, ça s'arrangera."))
                else:
                    sortie.append(Evenement(p.telegram_id, f"🎂 Joyeux anniversaire {p.prenom} ! Ton cadeau : "
                                            + improviser_ligne(session, archive, generateur), (archive.type, archive.file_id)))
    return sortie
