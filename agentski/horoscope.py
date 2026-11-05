"""L'horoscope de Krok : absurde, fixé pour la journée, sans le moindre rapport avec le ski. Cœur indépendant de Telegram.

Le signe vient de l'anniversaire s'il est connu ; sinon Krok attribue un faux signe, toujours le même pour la même personne."""
import json
import random
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
Il est absurde, taquin et jamais méchant. Deux à quatre phrases, pas de liste, tutoiement, un emoji au plus. Le chiffre et la couleur du jour sont ajoutés après ton texte : n'en parle pas.
Tu n'inventes aucun fait réel sur la personne (famille, santé, argent, amours, travail). Aucune allusion au ski, à la neige, aux pistes ni aux chutes.
Chaque horoscope doit être très différent des précédents : autre structure, autres images, autre chute. Évite les formules toutes faites (« les astres », « aujourd'hui, tu… »)."""

ANGLES = ["une alerte météo, mais pour l'humeur", "une critique de restaurant écrite par un astre vexé", "un message des astres mal traduit par un logiciel",
          "un communiqué administratif des planètes", "un mode d'emploi absurde de la journée", "une rencontre improbable avec un objet du quotidien",
          "un faux dicton de grand-mère, inventé sur le moment", "un commentaire de match de la journée à venir", "une petite annonce passée par l'univers",
          "une prédiction extrêmement précise et complètement inutile", "un conseil donné par un animal qui n'y connaît rien", "un règlement de copropriété cosmique",
          "une notice de médicament pour la journée", "un SMS envoyé par Jupiter", "un bulletin d'information télévisé trop sérieux",
          "une lettre de réclamation adressée à la Lune", "un classement des pires idées du jour", "un tuto de cuisine qui part en vrille"]
TONS = ["solennel", "complice", "blasé", "enthousiaste à l'excès", "mystérieux", "pince-sans-rire", "dramatique", "ultra concret"]
COULEURS = ["moutarde", "beige", "bleu canard", "vert sapin", "rose bonbon", "gris souris", "orange sécurité", "violet aubergine", "blanc cassé", "rouge tomate",
            "jaune poussin", "bordeaux", "turquoise", "kaki", "lavande"]
RECENTS = 8  # nombre d'horoscopes récents rappelés au modèle pour qu'il ne se répète pas

SECOURS = ["Une surprise t'attend, mais elle est petite et elle est dans ta poche.",
           "Les astres hésitent entre te sourire et regarder ailleurs. Fais comme eux."]


def signe(personne: Personne) -> str:
    if personne.anniversaire is None:
        return FAUX_SIGNES[personne.id % len(FAUX_SIGNES)]
    jour = (personne.anniversaire.month, personne.anniversaire.day)
    nom = SIGNES[-1][1]  # avant le 20 janvier : Capricorne
    for debut, libelle in SIGNES:
        if jour >= debut:
            nom = libelle
    return nom


def _recents(session: Session) -> list[str]:
    brut = reglage(session, "horoscopes_recents")
    return json.loads(brut) if brut else []


def horoscope(session: Session, personne: Personne, generateur, jour: date | None = None, hasard=random) -> str:
    """L'horoscope du jour : le même toute la journée pour la même personne. L'angle, le ton, le chiffre et la couleur sont tirés au sort ici,
    et les horoscopes récents sont rappelés au modèle pour qu'il ne se répète pas."""
    jour = jour or aujourdhui()
    cle = f"horoscope:{personne.id}:{jour}"
    deja = reglage(session, cle)
    if deja:
        return deja
    nom = nom_krok(personne)
    mon_signe = signe(personne)
    recents = _recents(session)
    demande = (f"Personne : {nom}.\nSigne : {mon_signe}.\nDate : {jour:%d/%m/%Y}.\nForme imposée : {hasard.choice(ANGLES)}.\nTon : {hasard.choice(TONS)}.\n"
               + (("Horoscopes récents, à ne surtout pas reprendre (ni leurs images, ni leur construction) :\n- " + "\n- ".join(recents) + "\n") if recents else "")
               + "Écris l'horoscope.")
    try:
        corps = generateur(SYSTEME, demande).strip()
    except Exception:
        corps = ""
    corps = corps or hasard.choice(SECOURS)
    texte = f"🔮 Horoscope du jour pour {nom} ({mon_signe}) :\n{corps}\n🍀 Chiffre du jour : {hasard.randint(1, 99)} · 🎨 Couleur du jour : {hasard.choice(COULEURS)}"
    definir_reglage(session, cle, texte)
    definir_reglage(session, "horoscopes_recents", json.dumps((recents + [corps])[-RECENTS:], ensure_ascii=False))
    return texte
