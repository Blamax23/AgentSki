"""Cœur des rappels, indépendant de Telegram : il lit et écrit la base et produit des messages (texte + boutons)."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config
from .horloge import maintenant, vers_paris, vers_utc
from .models import Descente, Journee, Personne, Presence, Rappel, Weekend

NOMS_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOMS_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
JOURS_PROPOSES = [1, 2, 3]  # décalage depuis le jeudi : vendredi, samedi, dimanche


@dataclass
class Message:
    texte: str
    boutons: list[list[tuple[str, str]]] = field(default_factory=list)  # lignes de (libellé, données)


@dataclass
class Envoi:
    rappel_id: int
    telegram_id: int | None
    message: Message


def _heure(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _jour_lisible(d: date) -> str:
    return f"{NOMS_JOURS[d.weekday()].capitalize()} {d.day} {NOMS_MOIS[d.month - 1]}"


def _de(prenom: str) -> str:
    """« de Maxime », mais « d'Adrien » et « d'Achille »."""
    return f"d'{prenom}" if prenom[0].lower() in "aeiouyhéèâî" else f"de {prenom}"


def organisateur(session: Session) -> Personne:
    return session.scalar(select(Personne).where(Personne.prenom == config.ORGANISATEUR))


# --- Planification ---------------------------------------------------------

def jeudi_courant() -> date:
    """Le jeudi de la semaine en cours (celui qui vient, ou celui qui vient de passer en fin de semaine)."""
    d = vers_paris(maintenant()).date()
    return d + timedelta(days=3 - d.weekday())


def planifier_jeudi(session: Session) -> None:
    """Garantit qu'une question est prévue pour le prochain jeudi."""
    local = vers_paris(maintenant())
    jour = local.date() + timedelta(days=(3 - local.weekday()) % 7)
    prevu = datetime.combine(jour, _heure(config.HEURE_JEUDI))
    if prevu <= local:
        prevu += timedelta(days=7)
    quand = vers_utc(prevu)
    orga = organisateur(session)
    existe = session.scalar(select(Rappel).where(Rappel.type == "jeudi", Rappel.envoyer_a == quand))
    if existe is None:
        session.add(Rappel(destinataire_id=orga.id, type="jeudi", envoyer_a=quand))
        session.commit()


def _planifier_journee(session: Session, journee: Journee) -> None:
    orga = organisateur(session)
    for type_, hhmm in (("matin", config.HEURE_MATIN), ("soir", config.HEURE_SOIR)):
        quand = vers_utc(datetime.combine(journee.jour, _heure(hhmm)))
        if quand > maintenant():
            session.add(Rappel(destinataire_id=orga.id, type=type_, journee_id=journee.id, envoyer_a=quand))


def _abandonner_rappels(session: Session, weekend: Weekend) -> None:
    ids = [j.id for j in weekend.journees]
    for r in session.scalars(select(Rappel).where(Rappel.journee_id.in_(ids), Rappel.envoye_a.is_(None),
                                                  Rappel.abandonne_a.is_(None))):
        r.abandonne_a = maintenant()


def _weekend_du_jeudi(session: Session, jeudi: date) -> Weekend | None:
    return session.scalar(select(Weekend).where(Weekend.date_debut > jeudi,
                                                Weekend.date_debut <= jeudi + timedelta(days=4)))


def enregistrer_weekend(session: Session, jeudi: date, jours: list[date], invites: list[int]) -> Weekend:
    """Crée ou met à jour le week-end, ses journées, les présences et les rappels du matin et du soir."""
    weekend = _weekend_du_jeudi(session, jeudi)
    if weekend is None:
        weekend = Weekend(date_debut=min(jours), date_fin=max(jours))
        session.add(weekend)
        session.flush()
    else:
        _abandonner_rappels(session, weekend)
        weekend.date_debut, weekend.date_fin = min(jours), max(jours)
    weekend.statut = "confirme"
    presents = [organisateur(session).id, *invites]

    for j in weekend.journees:
        j.ski = j.jour in jours
    for d in jours:
        journee = session.scalar(select(Journee).where(Journee.jour == d))
        if journee is None:
            journee = Journee(weekend_id=weekend.id, jour=d)
            session.add(journee)
            session.flush()
        journee.ski = True
        session.execute(delete(Presence).where(Presence.journee_id == journee.id))
        for pid in presents:
            session.add(Presence(journee_id=journee.id, personne_id=pid))
        _planifier_journee(session, journee)
    session.commit()
    return weekend


def annuler_weekend(session: Session, jeudi: date) -> None:
    weekend = _weekend_du_jeudi(session, jeudi)
    if weekend is not None:
        weekend.statut = "annule"
        for j in weekend.journees:
            j.ski = False
        _abandonner_rappels(session, weekend)
    session.commit()


# --- Envoi -----------------------------------------------------------------

def a_envoyer(session: Session) -> list[Envoi]:
    """Rappels dont l'heure est passée. Trop en retard : abandonnés plutôt qu'envoyés à contretemps."""
    envois = []
    rappels = session.scalars(select(Rappel).where(Rappel.envoye_a.is_(None), Rappel.abandonne_a.is_(None),
                                                   Rappel.envoyer_a <= maintenant()).order_by(Rappel.envoyer_a))
    for r in rappels:
        if maintenant() - r.envoyer_a > timedelta(minutes=config.RETARD_MAX_MIN):
            r.abandonne_a = maintenant()
            continue
        if r.type == "jeudi":
            jeudi_ = vers_paris(r.envoyer_a).date()
            from .agenda import jours_ski, masque_jours
            vus = jours_ski(jeudi_)  # un événement « Ski » dans l'agenda : la question confirme au lieu de demander
            msg = (ecran_jours(jeudi_, masque_jours(jeudi_, vus), "📅 Je vois « Ski » dans ton agenda : j'ai pré-coché les jours, valide ou corrige.")
                   if vus else ecran_jours(jeudi_, 0))
        elif r.type == "note":
            from .amis import ecran_note
            msg = ecran_note(r.journee_id)
        elif r.type == "reprise":
            from .entretien import message_reprise
            msg = message_reprise(session, r.journee_id, r.destinataire_id)
            if msg is None:  # entretien repris ou passé entre-temps
                r.abandonne_a = maintenant()
                continue
        elif r.type == "relance_jeudi":
            local = vers_paris(r.envoyer_a).date()
            msg = ecran_jours(local - timedelta(days=local.weekday() - 3), 0, relance=True)
        else:
            journee = session.get(Journee, r.journee_id)
            msg = texte_matin(session, journee) if r.type == "matin" else texte_soir(session, journee)
        r.texte = msg.texte
        envois.append(Envoi(r.id, session.get(Personne, r.destinataire_id).telegram_id, msg))
    session.commit()
    return envois


def marquer_envoye(session: Session, rappel_id: int) -> None:
    rappel = session.get(Rappel, rappel_id)
    rappel.envoye_a = maintenant()
    if rappel.type == "jeudi":
        _planifier_relances(session, rappel)
    session.commit()


def _planifier_relances(session: Session, question: Rappel) -> None:
    """Une relance toutes les 3 h après l'envoi de la question, hors nuit, jusqu'au vendredi soir."""
    debut = vers_paris(question.envoye_a)
    fin = datetime.combine(debut.date() + timedelta(days=1), time(config.RELANCE_JUSQU_A_H, 0))
    t = debut + timedelta(hours=config.RELANCE_TOUTES_LES_H)
    while t <= fin:
        if config.RELANCE_DE_H <= t.hour < config.RELANCE_JUSQU_A_H:
            session.add(Rappel(destinataire_id=question.destinataire_id, type="relance_jeudi",
                               envoyer_a=vers_utc(t)))
        t += timedelta(hours=config.RELANCE_TOUTES_LES_H)


def _clore_relances(session: Session, jeudi: date) -> None:
    """La question a reçu sa réponse : les relances restantes de ce jeudi sont abandonnées."""
    debut = vers_utc(datetime.combine(jeudi, time(0, 0)))
    for r in session.scalars(select(Rappel).where(
            Rappel.type == "relance_jeudi", Rappel.envoye_a.is_(None), Rappel.abandonne_a.is_(None),
            Rappel.envoyer_a >= debut, Rappel.envoyer_a < debut + timedelta(days=2))):
        r.abandonne_a = maintenant()
    session.commit()


# --- Question du jeudi ------------------------------------------------------
# Les boutons portent tout l'état de la réponse en cours : j|<jeudi>|<jours>|<personnes>|<action>
# (masques de bits), si bien qu'un redémarrage du bot au milieu de la réponse ne perd rien.

def _data(jeudi: date, jmask: int, pmask: int, action: str) -> str:
    return f"j|{jeudi.isoformat()}|{jmask}|{pmask}|{action}"


def ecran_jours(jeudi: date, jmask: int, alerte: str = "", relance: bool = False) -> Message:
    ligne = []
    for i, dec in enumerate(JOURS_PROPOSES):
        d = jeudi + timedelta(days=dec)
        coche = "✅ " if jmask & (1 << i) else ""
        ligne.append((f"{coche}{NOMS_JOURS[d.weekday()].capitalize()}", _data(jeudi, jmask ^ (1 << i), 0, "j")))
    texte = "🎿 Tu skies ce week-end ? Coche les jours, puis valide."
    if relance:
        texte = "⏰ Je te relance : " + texte[2:].lstrip()
    if alerte:
        texte += f"\n\n{alerte}"
    return Message(texte, [ligne, [("Valider", _data(jeudi, jmask, 0, "vj")),
                                   ("Non, pas ce week-end", _data(jeudi, 0, 0, "non"))]])


def _invitables(session: Session) -> list[Personne]:
    return list(session.scalars(select(Personne).where(Personne.prenom != config.ORGANISATEUR,
                                                       Personne.actif.is_(True)).order_by(Personne.id)))


def ecran_personnes(session: Session, jeudi: date, jmask: int, pmask: int) -> Message:
    boutons = []
    for p in _invitables(session):
        coche = "✅ " if pmask & (1 << p.id) else ""
        boutons.append([(f"{coche}{p.prenom}", _data(jeudi, jmask, pmask ^ (1 << p.id), "p"))])
    boutons.append([("Valider", _data(jeudi, jmask, pmask, "vp"))])
    return Message("Qui vient avec toi ? Coche, puis valide (personne = tu skies seul).", boutons)


def repondre(session: Session, data: str) -> Message:
    """Traite un appui sur un bouton de la question du jeudi et renvoie le nouvel écran."""
    _, iso, jm, pm, action = data.split("|")
    jeudi, jmask, pmask = date.fromisoformat(iso), int(jm), int(pm)
    if action == "j":
        return ecran_jours(jeudi, jmask)
    if action == "non":
        annuler_weekend(session, jeudi)
        _clore_relances(session, jeudi)
        return Message("👌 Pas de ski ce week-end, je ne t'embête pas.")
    if action == "vj":
        if jmask == 0:
            return ecran_jours(jeudi, 0, "Coche au moins un jour, ou réponds « Non ».")
        from .agenda import alix_skie, reponse_alix
        alix = session.scalar(select(Personne).where(Personne.prenom == "Alix"))  # Alix a dit qu'elle skiait : pré-cochée
        deja = 1 << alix.id if alix is not None and alix_skie(reponse_alix(session, jeudi + timedelta(days=1))) else 0
        return ecran_personnes(session, jeudi, jmask, deja)
    if action == "p":
        return ecran_personnes(session, jeudi, jmask, pmask)
    # vp : on enregistre
    jours = [jeudi + timedelta(days=dec) for i, dec in enumerate(JOURS_PROPOSES) if jmask & (1 << i)]
    invites = [p for p in _invitables(session) if pmask & (1 << p.id)]
    enregistrer_weekend(session, jeudi, jours, [p.id for p in invites])
    _clore_relances(session, jeudi)
    qui = ", ".join(p.prenom for p in invites) or "personne d'autre"
    return Message(f"✅ Noté : {', '.join(_jour_lisible(d) for d in jours)}, avec {qui}.\n"
                   f"Je t'envoie un rappel à {config.HEURE_MATIN} et à {config.HEURE_SOIR} chaque jour de ski.")


# --- Textes des rappels -----------------------------------------------------

def _presents(session: Session, journee: Journee) -> list[Personne]:
    ids = [p.personne_id for p in journee.presences]
    return list(session.scalars(select(Personne).where(Personne.id.in_(ids)).order_by(Personne.id)))


def _premieres_venues(session: Session, journee: Journee) -> list[Personne]:
    """Présents qui n'ont aucune journée de ski confirmée avant celle-ci."""
    nouveaux = []
    for p in _presents(session, journee):
        avant = session.scalar(select(func.count()).select_from(Presence).join(Journee).join(Weekend).where(
            Presence.personne_id == p.id, Journee.jour < journee.jour, Journee.ski.is_(True),
            Weekend.statut == "confirme"))
        if avant == 0:
            nouveaux.append(p)
    return nouveaux


def _conseil_pov(session: Session, presents: list[Personne]) -> str:
    cameras = [p for p in presents if p.statut == "duo_saison"]
    if len(cameras) == 1:
        return f"Une seule GoPro aujourd'hui : celle {_de(cameras[0].prenom)}."
    if len(cameras) < 2:
        return "Aucune GoPro du duo n'est là aujourd'hui."
    compte = {p.prenom: session.scalar(select(func.count()).select_from(Descente).where(
        Descente.filmeur_id == p.id)) for p in cameras}
    (a, na), (b, nb) = sorted(compte.items(), key=lambda kv: kv[1])
    if na == nb == 0:
        return "Une seule GoPro allumée par descente ; pas encore d'historique, à vous de répartir."
    if na == nb:
        return f"Une seule GoPro allumée par descente ; {a} et {b} sont à égalité ({na} descentes chacun)."
    return f"Une seule GoPro allumée par descente ; pour équilibrer, {a} filme en priorité ({na} descentes contre {nb} pour {b})."


def texte_matin(session: Session, journee: Journee) -> Message:
    presents = _presents(session, journee)
    lignes = [f"☀️ {_jour_lisible(journee.jour)} — rappel du matin",
              f"Présents : {', '.join(p.prenom for p in presents)}.", "",
              "• GoPro chargées, cartes SD vidées, batteries de rechange.",
              f"• {_conseil_pov(session, presents)}",
              "• Activité ski lancée sur la Garmin."]
    from .plans import idees_pour_rappel
    idees = idees_pour_rappel(session)
    if idees:
        lignes += ["", "💡 Idées de la boîte :"] + [f"• {i}" for i in idees]
    for p in _premieres_venues(session, journee):
        if p.prenom != config.ORGANISATEUR:
            lignes.append(f"• Première venue {_de(p.prenom)} : relever ses repères (chrono sur une piste repère, note sur 10).")
    return Message("\n".join(lignes))


def texte_soir(session: Session, journee: Journee) -> Message:
    lignes = [f"🌙 {_jour_lisible(journee.jour)} — rappel du soir", "",
              "• Rushes : GoPro déchargées sur le disque dur, puis remises en charge.",
              "• Entretien du soir : raconte ta journée en vocal pour le journal."]
    for p in _premieres_venues(session, journee):
        if p.prenom != config.ORGANISATEUR:
            lignes.append(f"• Repères {_de(p.prenom)} relevés ?")
    lendemain = session.scalar(select(Journee).where(Journee.jour == journee.jour + timedelta(days=1),
                                                     Journee.ski.is_(True)))
    if lendemain is not None:
        lignes.append(f"• Demain on skie encore : rappel à {config.HEURE_MATIN}.")
    return Message("\n".join(lignes), [[("🎙 Commencer l'entretien", f"e|go|{journee.id}")]])
