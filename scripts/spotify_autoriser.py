"""Autorise le bot à gérer des playlists et à commander la lecture (/play, /pause) sur ton compte Spotify (une seule fois, sur ton Mac) et écrit SPOTIFY_REFRESH_TOKEN dans .env.

Avant : crée une application sur https://developer.spotify.com/dashboard (compte Premium requis), ajoute l'adresse
http://127.0.0.1:8888/callback comme « Redirect URI », puis mets SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET dans .env.
Lancer : .venv/bin/python scripts/spotify_autoriser.py   (le jeton dure 6 mois : relance le script avant)"""
import base64
import json
import re
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ENV = RACINE / ".env"
REDIRECTION = "http://127.0.0.1:8888/callback"


def lire_env() -> dict:
    return dict(l.split("=", 1) for l in ENV.read_text().splitlines() if "=" in l and not l.startswith("#"))


env = lire_env()
client_id, secret = env.get("SPOTIFY_CLIENT_ID", ""), env.get("SPOTIFY_CLIENT_SECRET", "")
if not client_id or not secret:
    sys.exit("Renseigne d'abord SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET dans .env.")

code = {}
class Reception(BaseHTTPRequestHandler):
    def do_GET(self):
        code.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("C'est fait, tu peux fermer cet onglet.".encode())
    def log_message(self, *args):
        pass

webbrowser.open("https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
    "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECTION, "scope": "playlist-modify-private playlist-modify-public user-modify-playback-state user-read-playback-state"}))
serveur = HTTPServer(("127.0.0.1", 8888), Reception)
serveur.handle_request()
if "code" not in code:
    sys.exit(f"Autorisation refusée ou incomplète : {code.get('error')}")
requete = urllib.request.Request("https://accounts.spotify.com/api/token", data=urllib.parse.urlencode(
    {"grant_type": "authorization_code", "code": code["code"][0], "redirect_uri": REDIRECTION}).encode(), headers={
    "Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode(), "Content-Type": "application/x-www-form-urlencoded"})
jeton = json.load(urllib.request.urlopen(requete, timeout=20))["refresh_token"]
texte = ENV.read_text()
texte = re.sub(r"^SPOTIFY_REFRESH_TOKEN=.*\n?", "", texte, flags=re.M).rstrip("\n") + f"\nSPOTIFY_REFRESH_TOKEN={jeton}\n"
ENV.write_text(texte)
print("Autorisation enregistrée dans .env (SPOTIFY_REFRESH_TOKEN). Relance le bot.")
