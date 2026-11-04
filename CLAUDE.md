# Agent chronique ski

Bot Telegram qui aide à produire une série YouTube de chroniques de nos week-ends de ski au Grand Massif (Flaine, Les Carroz, Morillon, Samoëns, Sixt). Il rappelle quoi filmer, recueille les notes du soir, tient le journal de bord et anime le groupe.

**Source de vérité** : le document de référence https://claude.ai/artifact/EqTfUStuzHXPaADvQwc8Yo (architecture, décisions, coûts, risques, plan, checklist de l'étape 0). Le lire avec les outils Docs avant toute décision. Ce fichier n'en est qu'un résumé.

## Façon de travailler avec l'utilisateur

- Répondre en français.
- Poser les questions de clarification **une à la fois**.
- **Ne jamais présenter comme prévu ou validé quelque chose que l'utilisateur n'a pas validé explicitement.** Séparer clairement « validé », « écarté » et « en attente ».
- L'agent organise, propose et anime. Il ne décide jamais de l'histoire, ne choisit pas de personnage principal et **n'aide pas au montage** (pas de dérushage, sous-titres, brouillon de voix off, titres). Le montage est entièrement le travail de l'utilisateur.
- Pas de code avant que l'étape concernée soit lancée par l'utilisateur. Vérifier les tarifs et limites d'API sur les sources officielles avant de les affirmer.
- L'utilisateur est développeur et maintient le projet seul : privilégier la simplicité.

## Personnes

- **L'utilisateur** et **Adrien** : forfait saison, skient souvent à deux. Chacun a une GoPro sur casque (une seule allumée par descente) et une Garmin Forerunner 255.
- **Achille** et **Icham** : viennent ponctuellement. **Aucune date d'arrivée supposée** : ils entrent dans la série le jour où ils sont présents. Le week-end annuel tous ensemble est prévu du 31 décembre au 3 janvier, mais ils peuvent venir avant.
- **Alix** : sœur d'Achille. On loge chez elle à chaque week-end ; elle skie parfois, selon un emploi du temps imprévisible.
- Consentement déjà obtenu de tous : aucune gestion dans l'agent, seulement une commande « supprime ça ».

## Décisions techniques

- **Canal** : Telegram (groupe + messages privés, pop-ups sur boutons). WhatsApp écarté.
- **Hébergement** : VPS dans l'UE (1 vCPU, 1-2 Go RAM, 20 Go, Ubuntu LTS), dédié au bot. Seul le port SSH est ouvert. lincea.app n'y est pas hébergé.
- **Application** : Python 3.12+, `python-telegram-bot` ou `aiogram` en long polling, liste blanche des identifiants Telegram, SQLite (WAL) + SQLAlchemy/SQLModel + Alembic, `systemd`, sauvegarde nocturne hors VPS, signal de surveillance.
- **Rappels** : une table « rappels à envoyer » lue chaque minute, fuseau Europe/Paris.
- **IA** : OpenRouter pour tout.
  - Transcription via `/api/v1/audio/transcriptions`. Conversion des vocaux OGG Opus en MP3 avec `ffmpeg`, puis découpage, à cause de la limite de 60 s par requête.
  - Modèle de langage avec sorties structurées et modèle de repli. Modèle économique pour l'extraction et les relances, meilleur modèle pour les easter eggs et le briefing.
  - ZDR activé sur les 5 groupes de modèles, plus `provider.zdr: true` par requête.
  - Clé du bot plafonnée.
- **Recherche web** : `:online` ou plugin `web` d'OpenRouter. Le ZDR ne la couvre pas, donc aucune donnée personnelle dans les requêtes.
- **Médias** : uniquement les fichiers de moins de 20 Mo reçus via Telegram. Seul le `file_id` est stocké, aucun média sur le VPS. Pas de Google Drive, pas de serveur Bot API local.
- **Rushes GoPro** : ils vont directement sur le disque dur de l'utilisateur et ne passent jamais par l'agent.

## Fonctionnalités validées (détail dans le document)

- **MVP**
  - Rappel du jeudi (« tu skies ce week-end ? qui vient ? », en lisant l'agenda).
  - Pendant un week-end de ski : un rappel le matin et un le soir sur les obligations de tournage.
  - Entretien du soir : vocal + relances, plafonnées à 6-8, + boutons, avec « passer » et « plus tard ». Rangé dans le journal, consultable par `/journal`.
- **Raccourcis** : `/info`, `/meteo`, `/avalanche`, `/pointmeteo` (point météo chaque matin en privé, sur demande), `/moment`, `/chute`, `/citation`, `/idee`, `/pari`, `/fils`, `/afaire`, `/stats`, `/gopro`, `/kit`, `/vues`, `/course [plat] [précisions]` (quantités selon le nombre de présents, une seule liste), `/chambre`, `/son`, `/rdv`, `/ou`, ajout d'easter egg.
- **Easter eggs** : réserve d'archives alimentée par tout le monde (média de moins de 20 Mo + qui est visé + description libre, pas forcément liée au ski). Envoi aléatoire avec une ligne improvisée : courte, moqueuse, taquine, le bot peut prendre des initiatives. Références :
  - « Bah alors Adrien, on a du mal à rester debout sur une verte ?? 🤣 »
  - « Achille, t'avais plus de paires ou t'as tiré au sort ce matin ?? 🤣 »
- **Autour des sorties** :
  - briefing du vendredi (secteurs selon les conditions, pistes à faire, défis) ;
  - défis et idées (soirées, activités), avec recherche web ;
  - récap fun après le week-end, uniquement avec des stats obtenues sans saisie : une stat manquante n'apparaît pas.
- **Connexions** :
  - Spotify (playlist sur le compte de l'utilisateur ; le mode développeur exige Premium) ;
  - agenda (lien iCal privé en lecture, flux iCal publié pour le groupe, question à Alix en privé) ;
  - position en direct Telegram + OpenStreetMap ;
  - clés API personnelles Garmin de l'utilisateur et d'Adrien, et envoi de fichiers FIT/GPX ouvert à tous ;
  - raccourcis téléphone (Siri, bouton Action, double tap).
- **Repères comparables** : uniquement ce que tout le monde peut produire (chronos sur les pistes repères, note sur 10, descentes déclarées).

## Écarté

Aide au montage, Strava, WhatsApp, Google Drive, Google Sheets, webcams, sondages, alerte bonnes conditions, conseils de trajet, forfaits des invités, dépenses partagées, serveur MCP, Notion, transfert d'e-mails, tableau de bord, Open GoPro.

## Plan et état d'avancement

| Étape | Dates | Contenu | État |
|---|---|---|---|
| 0 | 4-10 nov. | Préparation, sans code applicatif | **En cours** |
| 1 | 11-17 nov. | Socle (bot, base, sauvegardes, surveillance) | À faire |
| 2 | 18-24 nov. | Rappels | À faire |
| 3 | 25 nov.-4 déc. | Entretien du soir et journal | À faire |
| 4 | 5-10 déc. | Répétition générale, gel des fonctionnalités | À faire |
| — | 12-13 déc. | Premier week-end (utilisateur + Adrien) : rodage, corrections uniquement | — |
| 5a | Dès que les amis risquent de venir, au plus tard le 31 déc. | Accueil des amis, notes, médias, `/moment`, `/chute`, `/citation` | À faire |
| 5b | Déc.-janv. | Easter eggs, raccourcis, météo, briefing, récap, agenda, Spotify, raccourcis téléphone | À faire |
| 6 | Janvier et après | Analyses, Garmin, FIT/GPX, position | À faire |

### Étape 0 : où on en est (4 nov. 2026)

**Fait par Claude**
- Document de référence rédigé.
- Points ouverts vérifiés : transcription OpenRouter (25 Mo, 60 s, Opus non listé, `prompt` ignoré), ZDR, prix de la recherche web, API personnelle Garmin, Spotify Premium, API avalanche Météo-France.
- Checklist et protocole de test des vocaux ajoutés au document.

**À faire par l'utilisateur** (Claude ne crée pas de comptes et ne fait pas d'achats)
- Commander et sécuriser le VPS.
- Créer le bot avec BotFather.
- Compte OpenRouter : ZDR sur les 5 groupes, 10-15 $ de crédits, clé plafonnée.
- Compte sur le portail des API Météo-France.
- Jeton Garmin pour lui et pour Adrien.
- Vérifier qu'il a Spotify Premium.
- Enregistrer les 8 vocaux de test dans Telegram (textes A et B dans le document).

**Prochaine action proposée par Claude** : écrire un petit script jetable qui passe les 8 vocaux dans 2 modèles de transcription compatibles ZDR. Il compare les noms propres, les chiffres, le taux d'erreur sur les textes lus, le coût (`usage.cost`) et le temps de réponse. Attendre le feu vert de l'utilisateur.

**Encore ouvert**
- Gratuité de l'API avalanche Météo-France.
- Liste exacte des modèles de transcription compatibles ZDR (`/api/v1/endpoints/zdr`).
- Limite de 20 Mo de l'API Bot Telegram, à relire sur la documentation officielle.
