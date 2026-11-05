# Agent chronique ski

Bot Telegram qui aide à produire la série YouTube de nos week-ends de ski au Grand Massif. Le plan, les décisions et le
rapport de développement sont dans le document de référence ; `CLAUDE.md` en est le résumé.

## Installer sur une machine neuve
Il faut Python 3.12 ou plus (testé en 3.14) et `ffmpeg` (`brew install ffmpeg` sur Mac, `apt install ffmpeg` sous Linux).
Sous Windows, passer par WSL : `run.sh` est un script shell.

```bash
git clone https://github.com/Blamax23/AgentSki.git && cd AgentSki
cp .env.exemple .env      # puis remplir .env
./run.sh                  # crée l'environnement au premier lancement, applique les migrations, démarre le bot
```

## Ce qui n'est PAS dans git (à copier à part)
- `.env` : token Telegram, clés, question d'accès. À copier à la main, en droits `600`.
- `data/` : la base SQLite du journal et les pistes apprises. Sans elle, le bot repart d'une base vide : le journal est perdu et
  chaque ami doit répondre de nouveau à la question d'accès. À sauvegarder.

## Une seule instance à la fois
Deux bots avec le même token se disputent les messages (erreur « Conflict »). Arrêter l'ancien avant de lancer le nouveau.

## Tests
`pip install -r requirements-dev.txt` une fois (il génère de vrais fichiers FIT de test), puis `./scripts/tout_tester.sh` : aucun test
ne contacte un vrai compte (Spotify, agenda, YouTube, OpenRouter). Les cartes OpenStreetMap et la météo ne sont appelées qu'en usage réel.
