"""Les messages d'avant-départ : 30, 14 et 7 jours avant le week-end annuel, chacun reçoit en privé quelques questions pour la série
et l'envie de laisser des vocaux, que le bot garde. Le bot propose des questions, il ne décide d'aucune histoire.
Cœur indépendant de Telegram : le hasard est injectable."""
import random
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .amis import definir_reglage, reglage
from .horloge import maintenant, vers_paris
from .medias import nombre_vocaux
from .models import Personne
from .rappels import _heure
from .surprises import Evenement

JALONS = (30, 14, 7)

QUESTIONS = {
    30: ["Ce que tu attends le plus de ce week-end.", "Ton meilleur souvenir avec le groupe.", "Pourquoi une série sur nos week-ends, selon toi ?",
         "Ce que tu veux absolument filmer.", "Celui ou celle qui va le plus nous faire rire, et pourquoi.", "Ta pire galère de ski à ce jour."],
    14: ["Ton pronostic : qui tombera en premier ?", "Le défi que tu lances au groupe.", "Ce que tu redoutes secrètement.",
         "Le surnom que tu donnes à ce week-end.", "Ce que tu espères qu'on dira de toi.", "Ton plus gros fou rire avec l'un d'entre nous."],
    7: ["Ton niveau de motivation sur 10, et pourquoi.", "Ce que tu as le plus hâte de manger.", "Ta résolution pour le week-end.",
        "Un message à toi-même, pour le réécouter plus tard.", "Ce que tu ne veux surtout pas rater.", "Ce que tu emportes et qu'on ne devrait pas voir."],
}

MATERIEL = ("GoPro chargée, carte mémoire vidée, batterie de rechange, Garmin chargée, chargeur dans le sac.")

ENTETES = {
    30: "🎬 {p}, J-{restant} avant le week-end annuel !\n"
        "Dans un mois, on tourne. Mais la série se construit dès maintenant : ta voix d'aujourd'hui sera la matière de demain.",
    14: "🔥 {p}, plus que {restant} jours !\n"
        "On approche. C'est le moment de laisser une trace de l'avant-départ : l'ambiance, les pronostics, les craintes.",
    7: "⛷ {p}, J-{restant} ! Dernière ligne droite.\n"
       "Dans une semaine, on y est. Un dernier vocal « moi, avant le départ », et dans un an tu seras content de t'entendre.",
}


def _questions(personne: Personne, jalon: int) -> list[str]:
    return random.Random(personne.id * 100 + jalon).sample(QUESTIONS[jalon], 3)  # les mêmes si le message est recalculé


def texte(session: Session, personne: Personne, jalon: int, restant: int) -> str:
    perso, groupe = nombre_vocaux(session, personne), nombre_vocaux(session)
    lignes = [ENTETES[jalon].format(p=personne.prenom, restant=restant), "",
              "🎙 Envoie-moi un vocal : 20 secondes suffisent, en voiture, sous la douche, peu importe. Je le garde, rien n'est perdu.",
              "Trois questions au choix, ou ce qui te passe par la tête :"]
    lignes += [f"• {q}" for q in _questions(personne, jalon)]
    lignes.append("")
    if perso:
        lignes.append(f"Tu as déjà {perso} vocal{'aux' if perso > 1 else ''} de gardé{'s' if perso > 1 else ''}. On continue ?")
    else:
        lignes.append("Tu n'as pas encore envoyé de vocal : lance-toi, le premier est toujours le plus drôle.")
    if groupe and jalon != 30:
        lignes.append(f"À eux tous, le groupe en a déjà envoyé {groupe}. Tu ne vas pas rester derrière ?")
    if jalon == 7:
        lignes += ["", f"🎒 Pour ne rien rater : {MATERIEL}"]
    lignes += ["", "Parle comme tu parles, il n'y a pas de bonne réponse."]
    return "\n".join(lignes)


def evenements(session: Session) -> list[Evenement]:
    """À l'heure du compte à rebours, le message du jalon le plus récent atteint, une seule fois par personne. Les jalons manqués sont sautés."""
    local = vers_paris(maintenant())
    fin = date.fromisoformat(config.DATE_WEEKEND_ANNUEL)
    if local.date() >= fin or local.time() < _heure(config.HEURE_COMPTE_A_REBOURS):
        return []
    restant = (fin - local.date()).days
    atteints = [j for j in JALONS if restant <= j]
    if not atteints:
        return []
    jalon = min(atteints)
    sortie = []
    for p in session.scalars(select(Personne).where(Personne.telegram_id.is_not(None)).order_by(Personne.id)):
        if reglage(session, f"compte:{jalon}:{p.id}") is not None:
            continue
        for j in atteints:
            definir_reglage(session, f"compte:{j}:{p.id}", "1")  # une seule fois, et les jalons précédents ne sont pas rattrapés
        sortie.append(Evenement(p.telegram_id, texte(session, p, jalon, restant)))
    return sortie
