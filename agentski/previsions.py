"""Couche Telegram de la météo : /info, /meteo, /avalanche, /pointmeteo."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from . import acces, briefing, meteo
from .amis import aujourdhui
from .db import SessionLocal
from .horloge import maintenant, vers_paris

STATIONS_BOUTONS = [[(s, f"i|{s}")] for s in meteo.STATIONS_GEO]


def _station_et_reste(args: list[str]) -> tuple[str | None, str]:
    """« Les Carroz demain » : la station peut avoir plusieurs mots, le reste désigne le jour."""
    for n in range(min(2, len(args)), 0, -1):
        station = meteo.trouver_station(" ".join(args[:n]))
        if station:
            return station, " ".join(args[n:])
    return None, " ".join(args)


async def _repondre_meteo(message, fonction, *args) -> None:
    await message.reply_text("🔎 Je regarde…")
    try:
        texte = await asyncio.to_thread(fonction, *args)
    except Exception:
        texte = "Je n'arrive pas à joindre le service météo pour l'instant, réessaie dans un moment."
    await message.reply_text(texte)


async def commande_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    station, _ = _station_et_reste(context.args)
    if station is None:
        await update.message.reply_text("Quelle station ?", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(t, callback_data=d) for t, d in ligne] for ligne in STATIONS_BOUTONS]))
        return
    await _repondre_meteo(update.message, meteo.texte_info, station, vers_paris(maintenant()))


async def bouton_station(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    station = query.data.split("|", 1)[1]
    await query.answer()
    await query.edit_message_text(f"{station}…")
    await _repondre_meteo(query.message, meteo.texte_info, station, vers_paris(maintenant()))


async def commande_meteo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    station, reste = _station_et_reste(context.args)
    if station is None:
        await update.message.reply_text("Format : /meteo Flaine demain, /meteo Samoëns samedi, /meteo Sixt 12/12")
        return
    jour = meteo.trouver_jour(reste, aujourdhui())
    if jour is None:
        await update.message.reply_text("Quel jour ? « demain », « samedi » ou « 12/12 ».")
        return
    await _repondre_meteo(update.message, meteo.texte_meteo, station, jour)


async def commande_avalanche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        await update.message.reply_text(briefing.texte_avalanche(session))


async def commande_pointmeteo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        personne = acces.personne_connue(session, update.effective_user.id)
        await update.message.reply_text(briefing.regler_point_meteo(session, personne, context.args))


def installer(app) -> None:
    app.add_handler(CommandHandler("info", commande_info))
    app.add_handler(CommandHandler("meteo", commande_meteo))
    app.add_handler(CommandHandler("avalanche", commande_avalanche))
    app.add_handler(CommandHandler("pointmeteo", commande_pointmeteo))
    app.add_handler(CallbackQueryHandler(bouton_station, pattern=r"^i\|"))
