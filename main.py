import os
import re
import asyncio
import logging
import traceback
from datetime import datetime
from threading import Thread
from importlib import import_module

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from src.configs.settings import TELEGRAM_BOT_TOKEN as TOKEN, ADMIN_IDS

from src.database.user_data_manager import (
    load_user_data,
    save_user_data,
    get_user_categories,
    toggle_user_category,
    get_user_min_discount,
    set_user_min_discount,
    get_user_days_limit,
    set_user_days_limit,
    get_user_post_interval,
    set_user_post_interval,
    get_user_offers_per_cycle,
    set_user_offers_per_cycle,
    get_user_buffer_clear_days,
    set_user_buffer_clear_days,
)

from src.utils.instagram_integration import (
    show_instagram_menu,
    handle_instagram_token,
    handle_instagram_callback,
)

from src.utils.product import (
    extract_asin_from_url,
    build_offer_message,
    Product,
)

from src.buffer.refill_base import refill_buffer_for_user
from src.buffer.buffer_manager import delete_buffer_file
from src.autoposting import start_scheduler
from src.utils.image_builder import crea_immagine_offerta_da_url
from src.utils.radar_price_error_detector import scan_price_errors


# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("log.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# COSTANTI UI
# -----------------------------------------------------------------------------
CATEGORIES = [
    ("Abbigliamento 👗", "cat_abbigliamento"),
    ("Elettronica 🔌", "cat_elettronica"),
    ("Casa e cucina 🍽️", "cat_casa_cucina"),
    ("Bellezza 💅", "cat_bellezza"),
    ("Sport ⚽", "cat_sport"),
    ("Giocattoli 🧸", "cat_giocattoli"),
    ("Fai da te 🔧", "cat_faidate"),
    ("Auto e Moto 🚗", "cat_auto_moto"),
    ("Libri e Kindle 📚", "cat_libri"),
    ("Videogiochi 🎮", "cat_videogiochi"),
    ("Alimentari 🛒", "cat_alimentari"),
    ("Animali 🐾", "cat_animali"),
    ("Offerte Amazon 💥", "cat_deals"),
    ("Offerte del giorno 🆕", "cat_goldbox"),
    ("Offerte lampo ⚡️", "cat_all"),
]

INFO_BUTTON = ("ℹ️ Informazioni", "info")
SETTINGS_BUTTON = ("⚙️ Impostazioni", "settings")
CATEGORY_BUTTON = ("📂 Categorie", "show_categories")
FUNCTIONS_BUTTON = ("🛠️ Funzioni", "funzioni_menu")


# -----------------------------------------------------------------------------
# HELPER GENERALI
# -----------------------------------------------------------------------------
def license_is_valid(user_info: dict) -> bool:
    if not user_info.get("has_license", False):
        return False

    expires_str = user_info.get("license_expires")
    if not expires_str:
        return False

    try:
        expires = datetime.strptime(expires_str, "%Y-%m-%d").date()
    except ValueError:
        return False

    return expires >= datetime.now().date()


def get_user_info(user_id: int) -> dict:
    return load_user_data().get(str(user_id), {})


def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


async def require_admin(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("🚫 Comando riservato all’amministratore.")
        return False
    return True


def build_main_menu(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(CATEGORY_BUTTON[0], callback_data=CATEGORY_BUTTON[1])],
        [InlineKeyboardButton(SETTINGS_BUTTON[0], callback_data=SETTINGS_BUTTON[1])],
        [InlineKeyboardButton(FUNCTIONS_BUTTON[0], callback_data=FUNCTIONS_BUTTON[1])],
        [InlineKeyboardButton(INFO_BUTTON[0], callback_data=INFO_BUTTON[1])],
    ])


def build_connect_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Canali / Gruppi Telegram", callback_data="connect_telegram")],
        [InlineKeyboardButton("📘 Account Facebook", callback_data="connect_facebook")],
        [InlineKeyboardButton("📷 Account Instagram", callback_data="connect_instagram")],
        [InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")],
    ])


def build_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔽 Filtri di Sconto", callback_data="filters")],
        [InlineKeyboardButton("⏱️ Tempi", callback_data="timing_menu")],
        [InlineKeyboardButton("🔗 Collega Canali", callback_data="connect_menu")],
        [InlineKeyboardButton("🎟️ Licenza & Affiliazione", callback_data="license_menu")],
        [InlineKeyboardButton("🔙 Torna al Menu", callback_data="back_to_menu")],
    ])


def build_functions_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Link Manuale", callback_data="manual_link")],
        [InlineKeyboardButton("🔙 Torna al Menu", callback_data="back_to_menu")],
    ])


def build_category_keyboard(user_id: int, columns: int = 2) -> InlineKeyboardMarkup:
    selected = get_user_categories(user_id)
    keyboard = []
    row = []

    for i, (label, code) in enumerate(CATEGORIES, 1):
        button_label = f"✅ {label}" if code in selected else label
        row.append(InlineKeyboardButton(text=button_label, callback_data=code))
        if i % columns == 0:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Torna al Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)


def build_timing_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📆 Intervallo Pubblicazioni", callback_data="set_interval")],
        [InlineKeyboardButton("🔢 Numero offerte per ciclo", callback_data="set_offers_count")],
        [InlineKeyboardButton("🕒 Giorni prima della ripubblicazione", callback_data="set_days")],
        [InlineKeyboardButton("📅 Giorni e Orari Attivi", callback_data="set_days_hours")],
        [InlineKeyboardButton("🧹 Pulizia Buffer Automatica", callback_data="set_buffer_clear")],
        [InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")],
    ])


def build_welcome_text(first_name: str) -> str:
    return (
        f"👋 Ciao *{first_name}*!\n\n"
        "🛍️ *Benvenuto nel Bot Offerte Amazon*\n\n"
        "Con questo bot puoi:\n"
        "• scegliere le categorie da monitorare\n"
        "• impostare lo sconto minimo\n"
        "• pubblicare offerte nei tuoi canali o social\n"
        "• usare funzioni manuali per post veloci\n\n"
        "👨‍💻 Bot creato da @Gianluca85\n\n"
        "👇 Seleziona un’opzione dal menu:"
    )


def clear_all_waiting_flags(context: ContextTypes.DEFAULT_TYPE):
    keys_to_remove = [
        "awaiting_manual_link",
        "awaiting_threshold_for",
        "awaiting_tag",
        "awaiting_fb_url",
        "awaiting_fb_page_id",
        "awaiting_fb_token",
        "awaiting_start_time",
        "awaiting_end_time",
        "awaiting_channel_username",
        "awaiting_channel_removal",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)


async def safe_delete_message(bot, chat_id: int, message_id: int | None):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def has_instagram_waiting_state(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(str(k).startswith("awaiting_instagram") for k in context.user_data.keys())


# -----------------------------------------------------------------------------
# COMANDI WATCHLIST
# -----------------------------------------------------------------------------
async def track_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args or len(args) < 2:
        return await update.message.reply_text(
            "Uso corretto:\n`/track <ASIN> <prezzo_soglia>`\n\nEsempio:\n`/track B09XYZ1234 25.99`",
            parse_mode=ParseMode.MARKDOWN
        )

    asin = args[0].strip()
    try:
        threshold = float(args[1].replace(",", "."))
    except Exception:
        return await update.message.reply_text("❌ Prezzo soglia non valido.")

    data = load_user_data()
    user_data = data.setdefault(str(user_id), {})
    watchlist = user_data.setdefault("watchlist", [])

    if any(x["asin"] == asin for x in watchlist):
        return await update.message.reply_text(f"ℹ️ `{asin}` è già nella watchlist.", parse_mode=ParseMode.MARKDOWN)

    watchlist.append({"asin": asin, "threshold": threshold})
    save_user_data(data)

    await update.message.reply_text(
        f"✅ Aggiunto *{asin}* con soglia *{threshold:.2f}€*",
        parse_mode=ParseMode.MARKDOWN
    )


async def untrack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args:
        return await update.message.reply_text("Uso corretto:\n`/untrack <ASIN>`", parse_mode=ParseMode.MARKDOWN)

    asin = args[0].strip()
    data = load_user_data()
    user_data = data.get(str(user_id), {})
    watchlist = user_data.get("watchlist", [])

    new_watchlist = [x for x in watchlist if x["asin"] != asin]
    if len(new_watchlist) == len(watchlist):
        return await update.message.reply_text(f"❌ `{asin}` non trovato nella watchlist.", parse_mode=ParseMode.MARKDOWN)

    user_data["watchlist"] = new_watchlist
    save_user_data(data)
    await update.message.reply_text(f"🗑️ Rimosso `{asin}` dalla watchlist.", parse_mode=ParseMode.MARKDOWN)


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = load_user_data().get(str(user_id), {})
    watchlist = user_data.get("watchlist", [])

    if not watchlist:
        return await update.message.reply_text("📭 La tua watchlist è vuota.")

    lines = [f"• `{item['asin']}` ➜ *{item['threshold']:.2f}€*" for item in watchlist]
    text = "📋 *La tua watchlist:*\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------------
# REFILL ASINCRONO BUFFER
# -----------------------------------------------------------------------------
async def async_refill_and_notify(user_id: int, category_code: str):
    scrapers = {
        "cat_elettronica":   ("src.scraper.electronics_scraper", "get_asins_from_electronics"),
        "cat_deals":         ("src.scraper.deals_scraper", "get_deals_asins"),
        "cat_abbigliamento": ("src.scraper.abbigliamento_scraper", "get_asins_from_abbigliamento"),
        "cat_casa_cucina":   ("src.scraper.casa_cucina_scraper", "get_asins_from_casa_cucina"),
        "cat_bellezza":      ("src.scraper.bellezza_scraper", "get_asins_from_bellezza"),
        "cat_sport":         ("src.scraper.sport_scraper", "get_asins_from_sport"),
        "cat_giocattoli":    ("src.scraper.giocattoli_scraper", "get_asins_from_giocattoli"),
        "cat_faidate":       ("src.scraper.faidate_scraper", "get_asins_from_faidate"),
        "cat_auto_moto":     ("src.scraper.auto_moto_scraper", "get_asins_from_auto_moto"),
        "cat_libri":         ("src.scraper.libri_scraper", "get_asins_from_libri"),
        "cat_videogiochi":   ("src.scraper.videogiochi_scraper", "get_asins_from_videogiochi"),
        "cat_alimentari":    ("src.scraper.alimentari_scraper", "get_asins_from_alimentari"),
        "cat_animali":       ("src.scraper.animali_scraper", "get_asins_from_animali"),
        "cat_goldbox":       ("src.scraper.offerte_giorno_scraper", "get_offerte_giorno_asins"),
        "cat_all":           ("src.scraper.all_scraper", "get_asins_from_all"),
    }

    try:
        mod_path, func_name = scrapers[category_code]
        scraper_module = import_module(mod_path)
        scraper_func = getattr(scraper_module, func_name)

        await asyncio.to_thread(
            refill_buffer_for_user,
            user_id,
            category_code,
            scraper_func
        )

        logger.info(f"[REFILL] Completato per user={user_id}, category={category_code}")

    except Exception:
        logger.exception(f"[REFILL] Errore per user={user_id}, category={category_code}")


# -----------------------------------------------------------------------------
# START
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_all_waiting_flags(context)

    await update.message.reply_text(
        build_welcome_text(user.first_name),
        reply_markup=build_main_menu(user.id),
        parse_mode=ParseMode.MARKDOWN
    )


# -----------------------------------------------------------------------------
# GESTIONE MESSAGGI TESTO
# -----------------------------------------------------------------------------
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    logger.info(f"[TEXT] user={user_id} text={text!r} flags={dict(context.user_data)}")

    # -------------------------------------------------------------------------
    # Instagram fallback: delega al modulo se è in attesa di token/config
    # -------------------------------------------------------------------------
    if has_instagram_waiting_state(context):
        try:
            await handle_instagram_token(update, context)
            return
        except Exception:
            logger.exception("[INSTAGRAM] Errore nella gestione token Instagram")

    # -------------------------------------------------------------------------
    # LINK MANUALE
    # -------------------------------------------------------------------------
    if context.user_data.pop("awaiting_manual_link", False):
        from src.scraper.product_scraper import extract_product_info
        from src.utils.extract_product_info_selenium import extract_product_info_selenium
        from src.autoposting import send_single_offer

        prompt_id = context.user_data.pop("manual_prompt_msg_id", None)
        await safe_delete_message(context.bot, chat_id, prompt_id)

        link = text.strip()
        asin = extract_asin_from_url(link)

        if not asin:
            await update.message.reply_text(
                "❌ Link non valido.\nInviami un URL Amazon completo, ad esempio:\n`https://www.amazon.it/dp/B012345678`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        loading_msg = await update.message.reply_text("⏳ Recupero prodotto in corso...")

        prod = None

        # 1) Prima provo la pipeline standard: PA-API -> fallback HTML
        try:
            prod = extract_product_info(asin)
        except Exception:
            logger.exception(f"[MANUAL_LINK] Errore extract_product_info per ASIN {asin}")

        # 2) Se non ho un prezzo valido, provo Selenium direttamente sul link manuale
        price_ok = False
        try:
            price_ok = float(str(getattr(prod, "price", 0)).replace(",", ".")) > 0
        except Exception:
            price_ok = False

        if not prod or not price_ok:
            logger.warning(f"[MANUAL_LINK] Prezzo non valido da pipeline standard per ASIN {asin} -> fallback Selenium")
            try:
                prod = extract_product_info_selenium(link)
            except Exception:
                logger.exception(f"[MANUAL_LINK] Errore Selenium fallback per ASIN {asin}")
                prod = None

        # 3) Ricontrollo finale prezzo
        price_ok = False
        try:
            price_ok = float(str(getattr(prod, "price", 0)).replace(",", ".")) > 0
        except Exception:
            price_ok = False

        if not prod or not price_ok:
            await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)
            await update.message.reply_text("❌ Non sono riuscito a recuperare un prezzo valido per questo prodotto.")
            return

        if not getattr(prod, "image", None):
            await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)
            await update.message.reply_text("⚠️ Dati trovati, ma immagine prodotto assente.")
            return

        img_path = None
        try:
            img_path = crea_immagine_offerta_da_url(
                url=prod.image,
                prezzo=prod.price,
                sconto=prod.discount,
                vecchio_prezzo=prod.old_price,
                asin=prod.asin
            )

            await send_single_offer(
                app=context.application,
                user_id=user_id,
                prod=prod,
                img_path=img_path
            )

            await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)

            try:
                await update.message.delete()
            except Exception:
                pass

            success_msg = await context.bot.send_message(
                chat_id=user_id,
                text="✅ Post manuale pubblicato con successo!"
            )
            await asyncio.sleep(3)
            await safe_delete_message(context.bot, success_msg.chat_id, success_msg.message_id)

        except Exception:
            logger.exception("[MANUAL_LINK] Errore durante creazione o invio post manuale")
            await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)
            await update.message.reply_text("❌ Errore durante la pubblicazione del post manuale.")
        finally:
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

        await context.bot.send_message(
            chat_id=user_id,
            text="📲 Scegli cosa vuoi fare adesso:",
            reply_markup=build_main_menu(user_id)
        )
        return

    # -------------------------------------------------------------------------
    # TRACCIA PREZZO
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_threshold_for"):
        asin = context.user_data.pop("awaiting_threshold_for")
        try:
            threshold = float(text.replace(",", "."))
        except ValueError:
            return await update.message.reply_text("❌ Prezzo non valido. Esempio corretto: `25.99`", parse_mode=ParseMode.MARKDOWN)

        data = load_user_data()
        user_data = data.setdefault(str(user_id), {})
        watchlist = user_data.setdefault("watchlist", [])

        if not any(x["asin"] == asin for x in watchlist):
            watchlist.append({"asin": asin, "threshold": threshold})
            save_user_data(data)

        return await update.message.reply_text(
            f"✅ Tracciamento attivato!\nTi avviserò quando `{asin}` scenderà a *{threshold:.2f}€* o meno.",
            parse_mode=ParseMode.MARKDOWN
        )

    # -------------------------------------------------------------------------
    # TAG AFFILIATO
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_tag"):
        data = load_user_data()
        uid = str(user_id)
        data.setdefault(uid, {})

        if not data[uid].get("has_license"):
            context.user_data["awaiting_tag"] = False
            await update.message.reply_text("❌ Non hai una licenza attiva.")
            return

        tag = text.strip()
        data[uid]["tag_id"] = tag
        save_user_data(data)
        context.user_data["awaiting_tag"] = False

        await update.message.reply_text(f"✅ Tag affiliato salvato: `{tag}`", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(2)
        await update.message.reply_text(
            "⚙️ *Impostazioni aggiornate.*",
            reply_markup=build_main_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FACEBOOK URL
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_fb_url"):
        if text.startswith("https://www.facebook.com/") or text.startswith("https://facebook.com/"):
            data = load_user_data()
            uid = str(user_id)
            data.setdefault(uid, {})
            data[uid].setdefault("facebook_config", {})
            data[uid]["facebook_config"]["url"] = text
            save_user_data(data)

            context.user_data["awaiting_fb_url"] = False
            context.user_data["awaiting_fb_page_id"] = True

            await update.message.reply_text("✅ URL salvato.\nOra inviami il *Page ID* della pagina Facebook.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ Inserisci un URL valido che inizi con `https://www.facebook.com/`", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # FACEBOOK PAGE ID
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_fb_page_id"):
        page_id = text.strip()

        data = load_user_data()
        uid = str(user_id)
        data.setdefault(uid, {})
        data[uid].setdefault("facebook_config", {})
        data[uid]["facebook_config"]["page_id"] = page_id
        save_user_data(data)

        context.user_data["awaiting_fb_page_id"] = False
        context.user_data["awaiting_fb_token"] = True

        await update.message.reply_text("✅ Page ID salvato.\nOra inviami il *token d’accesso* della pagina.", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # FACEBOOK TOKEN
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_fb_token"):
        token = text.strip()

        data = load_user_data()
        uid = str(user_id)
        data.setdefault(uid, {})
        data[uid].setdefault("facebook_config", {})
        data[uid]["facebook_config"]["access_token"] = token
        save_user_data(data)

        context.user_data["awaiting_fb_token"] = False

        await update.message.reply_text("✅ Pagina Facebook collegata con successo!")
        await asyncio.sleep(2)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔗 Scegli una piattaforma da configurare:",
            reply_markup=build_connect_menu()
        )
        return

    # -------------------------------------------------------------------------
    # ORARIO INIZIO
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_start_time"):
        if re.match(r"^\d{2}:\d{2}$", text):
            from src.configs.schedule_config import get_user_schedule, set_user_schedule

            schedule = get_user_schedule(user_id)
            schedule.setdefault("time_range", {})["start"] = text
            set_user_schedule(user_id, schedule)
            context.user_data["awaiting_start_time"] = False

            current_start = schedule["time_range"].get("start", "08:00")
            current_end = schedule["time_range"].get("end", "22:00")

            keyboard = [
                [InlineKeyboardButton(f"🟢 Inizio: {current_start}", callback_data="set_start_time")],
                [InlineKeyboardButton(f"🔴 Fine: {current_end}", callback_data="set_end_time")],
                [InlineKeyboardButton("🔙 Torna a Giorni/Orari", callback_data="set_days_hours")]
            ]

            await update.message.reply_text(
                f"✅ Orario di inizio impostato a *{text}*",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⏰ *Configura gli orari di pubblicazione automatica*\n\n"
                    f"• Inizio: `{current_start}`\n"
                    f"• Fine: `{current_end}`"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Formato non valido. Usa `HH:MM`, ad esempio `08:00`.", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # ORARIO FINE
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_end_time"):
        if re.match(r"^\d{2}:\d{2}$", text):
            from src.configs.schedule_config import get_user_schedule, set_user_schedule

            schedule = get_user_schedule(user_id)
            schedule.setdefault("time_range", {})["end"] = text
            set_user_schedule(user_id, schedule)
            context.user_data["awaiting_end_time"] = False

            current_start = schedule["time_range"].get("start", "08:00")
            current_end = schedule["time_range"].get("end", "22:00")

            keyboard = [
                [InlineKeyboardButton(f"🟢 Inizio: {current_start}", callback_data="set_start_time")],
                [InlineKeyboardButton(f"🔴 Fine: {current_end}", callback_data="set_end_time")],
                [InlineKeyboardButton("🔙 Torna a Giorni/Orari", callback_data="set_days_hours")]
            ]

            await update.message.reply_text(
                f"✅ Orario di fine impostato a *{text}*",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(1)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⏰ *Configura gli orari di pubblicazione automatica*\n\n"
                    f"• Inizio: `{current_start}`\n"
                    f"• Fine: `{current_end}`"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Formato non valido. Usa `HH:MM`, ad esempio `22:00`.", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # TELEGRAM ADD CHANNEL
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_channel_username"):
        if re.match(r"^@[\w\d_]{5,}$", text):
            data = load_user_data()
            uid = str(user_id)
            data.setdefault(uid, {})
            data[uid].setdefault("telegram_channels", [])

            if text not in data[uid]["telegram_channels"]:
                data[uid]["telegram_channels"].append(text)
                save_user_data(data)

            context.user_data["awaiting_channel_username"] = False

            msg = await update.message.reply_text(
                f"✅ Canale collegato correttamente: *{text}*",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(2)
            await safe_delete_message(context.bot, msg.chat_id, msg.message_id)

            await context.bot.send_message(
                chat_id=chat_id,
                text="🔗 Scegli una piattaforma da configurare:",
                reply_markup=build_connect_menu(),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                "❌ Username non valido.\nDeve iniziare con `@` e contenere almeno 5 caratteri.",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # -------------------------------------------------------------------------
    # TELEGRAM REMOVE CHANNEL BY TEXT
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_channel_removal"):
        to_remove = text if text.startswith("@") else f"@{text}"

        data = load_user_data()
        uid = str(user_id)
        channels = data.get(uid, {}).get("telegram_channels", [])

        if to_remove in channels:
            channels.remove(to_remove)
            data[uid]["telegram_channels"] = channels
            save_user_data(data)
            await update.message.reply_text(
                f"✅ Il canale *{to_remove}* è stato scollegato.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"❌ Il canale *{to_remove}* non era collegato.",
                parse_mode=ParseMode.MARKDOWN
            )

        context.user_data["awaiting_channel_removal"] = False
        await asyncio.sleep(2)
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔗 Scegli una piattaforma da configurare:",
            reply_markup=build_connect_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return


# -----------------------------------------------------------------------------
# TEST OFFERTA
# -----------------------------------------------------------------------------
async def test_offerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id

    product = Product(
        asin="B09B8V6Y9B",
        title="Echo Dot (5ª generazione) con Alexa",
        image="https://m.media-amazon.com/images/I/81NVGKN5pML._AC_SX522_.jpg",
        link="https://www.amazon.it/dp/B09B8V6Y9B",
        price="29.99",
        old_price="59.99",
        discount=50
    )

    file_path = crea_immagine_offerta_da_url(
        url=product.image,
        prezzo=f"{product.price}€",
        sconto=product.discount,
        vecchio_prezzo=f"{product.old_price}€",
        asin=product.asin
    )

    try:
        text, markup = build_offer_message(product, user_id, category_name="Echo Dot")
        markup.inline_keyboard.append([
            InlineKeyboardButton(
                text="🔔 Traccia prezzo",
                callback_data=f"track_{product.asin}"
            )
        ])

        with open(file_path, "rb") as photo_file:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo_file,
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )

        # Tentativo facoltativo Facebook, senza rompere il comando se manca il modulo
        try:
            from src.utils.product import get_affiliate_link
            from src.social.facebook_publisher import publish_to_facebook_file

            affiliate_link = get_affiliate_link(user_id, product.asin)
            caption_fb = (
                f"🔥 {product.title}\n"
                f"💰 {product.price}€ invece di {product.old_price}€ (-{product.discount}%)\n"
                f"👉 {affiliate_link}"
            )

            success = publish_to_facebook_file(user_id, file_path, caption_fb)
            if success:
                await update.message.reply_text("✅ Offerta pubblicata anche su Facebook!")
            else:
                await update.message.reply_text("⚠️ Invio Telegram ok, ma pubblicazione Facebook non riuscita.")
        except Exception:
            logger.info("[TEST_OFFERTA] Modulo Facebook o affiliate non disponibile, salto la pubblicazione extra.")

    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# RADAR
# -----------------------------------------------------------------------------
async def radar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import random
    from src.autoposting import send_single_offer

    user_id = update.effective_user.id
    loading_msg = await update.message.reply_text("🛒 Sto cercando offerte interessanti... attendi qualche secondo.")

    try:
        products = scan_price_errors(keywords="Offerte", min_discount=50, max_results=10)
    except Exception:
        logger.exception("[RADAR] Errore scansione")
        await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)
        await update.message.reply_text("❌ Errore durante la scansione Radar.")
        return

    await safe_delete_message(context.bot, loading_msg.chat_id, loading_msg.message_id)

    if not products:
        await update.message.reply_text("❌ Nessuna offerta rara trovata questa volta.\nRiprova più tardi.")
        return

    random.shuffle(products)

    for prod in products[:5]:
        img_path = None
        try:
            img_path = crea_immagine_offerta_da_url(
                url=prod.image,
                prezzo=prod.price,
                sconto=prod.discount,
                vecchio_prezzo=prod.old_price,
                asin=prod.asin
            )

            await send_single_offer(
                app=context.application,
                user_id=user_id,
                prod=prod,
                img_path=img_path
            )
        except Exception:
            logger.exception(f"[RADAR] Errore invio prodotto {getattr(prod, 'asin', 'unknown')}")
        finally:
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

    await update.message.reply_text("✅ Fine scansione Radar Offerte.")


# -----------------------------------------------------------------------------
# LICENZA ADMIN
# -----------------------------------------------------------------------------
async def attivalicenza_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from src.utils.license_manager import attiva_licenza

    if not await require_admin(update):
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❗ Usa: `/attivalicenza <user_id> <giorni>`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        user_id = int(context.args[0])
        giorni = int(context.args[1])

        attiva_licenza(user_id, giorni)
        await update.message.reply_text(f"✅ Licenza attivata per `{user_id}` per `{giorni}` giorni.", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.exception("[LICENZA] Errore attivazione")
        await update.message.reply_text(f"⚠️ Errore: `{e}`", parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------------
# ADMIN STATS
# -----------------------------------------------------------------------------
async def admin_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return

    try:
        import glob
        import json
        from src.configs.settings import BUFFER_PATH, LINK_MAP_PATH

        users = load_user_data()
        buffer_files = glob.glob(os.path.join(BUFFER_PATH, "*.json"))
        total_buffered = 0

        for path in buffer_files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    total_buffered += len(data)
            except Exception:
                pass

        link_count = 0
        if os.path.exists(LINK_MAP_PATH):
            try:
                with open(LINK_MAP_PATH, "r", encoding="utf-8") as f:
                    links = json.load(f)
                if isinstance(links, dict):
                    link_count = len(links)
            except Exception:
                pass

        active_categories = sum(len(u.get("categories", [])) for u in users.values() if isinstance(u, dict))
        text = (
            "📊 *Admin stats V2*\n\n"
            f"👤 Utenti: *{len(users)}*\n"
            f"📂 Categorie attive totali: *{active_categories}*\n"
            f"📦 Prodotti nei buffer: *{total_buffered}*\n"
            f"🔗 Link generati: *{link_count}*"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("[ADMIN_STATS] Errore")
        await update.message.reply_text(f"⚠️ Errore admin stats: `{e}`", parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------------
# TRACK BUTTON HANDLER
# -----------------------------------------------------------------------------
async def track_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    asin = query.data.split("_", 1)[1]
    context.user_data["awaiting_threshold_for"] = asin

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        pass

    await update.effective_chat.send_message(
        f"📊 Inserisci il prezzo soglia per l’ASIN `{asin}`\nEsempio: `25.99`",
        parse_mode=ParseMode.MARKDOWN
    )


# -----------------------------------------------------------------------------
# CALLBACK PRINCIPALI
# -----------------------------------------------------------------------------
async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    user_id = user.id

    await query.answer()

    # -------------------------------------------------------------------------
    # CALLBACK TRACK
    # -------------------------------------------------------------------------
    if data.startswith("track_"):
        return

    # -------------------------------------------------------------------------
    # INFO
    # -------------------------------------------------------------------------
    if data == "info":
        keyboard = [[InlineKeyboardButton("🔙 Torna al Menu", callback_data="back_to_menu")]]
        await query.edit_message_text(
            "ℹ️ *Come funziona il bot*\n\n"
            "• Seleziona le categorie che vuoi monitorare\n"
            "• Imposta il filtro minimo di sconto\n"
            "• Collega i tuoi canali o social\n"
            "• Il bot pubblicherà le offerte in automatico secondo le tue impostazioni",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # SETTINGS
    # -------------------------------------------------------------------------
    if data == "settings":
        await query.edit_message_text(
            "⚙️ *Impostazioni utente*\n\nScegli cosa configurare:",
            reply_markup=build_settings_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FUNZIONI
    # -------------------------------------------------------------------------
    if data == "funzioni_menu":
        await query.edit_message_text(
            "🛠️ *Funzioni personalizzate*\n\nSeleziona un’operazione:",
            reply_markup=build_functions_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # CONNECT MENU
    # -------------------------------------------------------------------------
    if data == "connect_menu":
        await query.edit_message_text(
            "🔗 *Collega i tuoi canali e social*\n\nScegli una piattaforma da configurare:",
            reply_markup=build_connect_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FACEBOOK
    # -------------------------------------------------------------------------
    if data == "connect_facebook":
        fb_data = get_user_info(user_id).get("facebook_config", {})
        url = fb_data.get("url")
        page_id = fb_data.get("page_id")
        token = fb_data.get("access_token")

        if url and page_id and token:
            text = (
                "📘 *Pagina Facebook collegata*\n\n"
                f"• URL: {url}\n"
                f"• Page ID: `{page_id}`\n"
                "• Token: ✅ salvato\n\n"
                "Puoi modificarla o scollegarla:"
            )
            keyboard = [
                [InlineKeyboardButton("🔄 Cambia configurazione", callback_data="fb_config_start")],
                [InlineKeyboardButton("❌ Scollega Pagina", callback_data="fb_unlink")],
                [InlineKeyboardButton("🔙 Torna a Collega Canali", callback_data="connect_menu")]
            ]
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            context.user_data["awaiting_fb_url"] = True
            await query.edit_message_text(
                "✏️ Inviami l’URL della tua pagina Facebook.\n\nEsempio:\n`https://www.facebook.com/tuapagina`",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    if data == "fb_config_start":
        context.user_data["awaiting_fb_url"] = True
        context.user_data["awaiting_fb_page_id"] = False
        context.user_data["awaiting_fb_token"] = False

        await query.edit_message_text(
            "✏️ Inviami il nuovo URL della tua pagina Facebook.\n\nEsempio:\n`https://www.facebook.com/tuapagina`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "fb_unlink":
        all_data = load_user_data()
        user_data = all_data.get(str(user_id), {})

        if "facebook_config" in user_data:
            del user_data["facebook_config"]
            save_user_data(all_data)

            await query.edit_message_text(
                "✅ Pagina Facebook scollegata correttamente.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text(
                "ℹ️ Nessuna pagina Facebook collegata.",
                parse_mode=ParseMode.MARKDOWN
            )

        await asyncio.sleep(2)
        await query.edit_message_text(
            "🔗 *Collega i tuoi canali e social*",
            reply_markup=build_connect_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # TELEGRAM CHANNELS
    # -------------------------------------------------------------------------
    if data == "connect_telegram":
        user_info = get_user_info(user_id)
        channels = user_info.get("telegram_channels", [])

        keyboard = []
        for ch in channels:
            keyboard.append([
                InlineKeyboardButton(f"❌ Rimuovi {ch}", callback_data=f"remove_channel_{ch[1:]}")
            ])

        keyboard.append([InlineKeyboardButton("➕ Aggiungi nuovo canale/gruppo", callback_data="add_channel")])
        keyboard.append([InlineKeyboardButton("🔙 Torna a Collega Canali", callback_data="connect_menu")])

        if channels:
            text = "📣 *Canali / Gruppi collegati*\n\n" + "\n".join(f"• {ch}" for ch in channels)
        else:
            text = "📣 *Nessun canale collegato*\n\nAggiungi un canale o gruppo inviando il relativo `@username`."

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "add_channel":
        context.user_data["awaiting_channel_username"] = True
        await query.edit_message_text(
            "✏️ Inviami l’`@username` del tuo canale o gruppo.\n\nEsempio:\n`@nomecanale`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("remove_channel_"):
        to_remove = "@" + data.split("_", 2)[2]

        all_data = load_user_data()
        user_data = all_data.get(str(user_id), {})
        channels = user_data.get("telegram_channels", [])

        if to_remove in channels:
            channels.remove(to_remove)
            all_data[str(user_id)]["telegram_channels"] = channels
            save_user_data(all_data)

            await query.edit_message_text(
                f"✅ Il canale *{to_remove}* è stato scollegato.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text(
                f"❌ Il canale *{to_remove}* non era collegato.",
                parse_mode=ParseMode.MARKDOWN
            )

        await asyncio.sleep(2)

        user_info = get_user_info(user_id)
        channels = user_info.get("telegram_channels", [])

        keyboard = []
        for ch in channels:
            keyboard.append([
                InlineKeyboardButton(f"❌ Rimuovi {ch}", callback_data=f"remove_channel_{ch[1:]}")
            ])

        keyboard.append([InlineKeyboardButton("➕ Aggiungi nuovo canale/gruppo", callback_data="add_channel")])
        keyboard.append([InlineKeyboardButton("🔙 Torna a Collega Canali", callback_data="connect_menu")])

        if channels:
            text = "📣 *Canali / Gruppi collegati*\n\n" + "\n".join(f"• {ch}" for ch in channels)
        else:
            text = "📣 *Nessun canale collegato*\n\nAggiungi un canale o gruppo inviando il relativo `@username`."

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # TEMPI
    # -------------------------------------------------------------------------
    if data == "timing_menu":
        await query.edit_message_text(
            "⏱️ *Gestione dei tempi*\n\nConfigura frequenza, attese e giorni attivi.",
            reply_markup=build_timing_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_interval":
        current = get_user_post_interval(user_id)
        keyboard = []
        row = []

        for val in [5, 10, 15, 20, 30, 60]:
            label = f"{val} minuti"
            if val == current:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"interval_{val}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Tempi", callback_data="timing_menu")])

        await query.edit_message_text(
            "📆 *Ogni quanti minuti vuoi pubblicare una nuova offerta?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("interval_"):
        value = int(data.split("_")[1])
        set_user_post_interval(user_id, value)

        await query.edit_message_text(
            f"✅ Intervallo aggiornato: *{value} minuti*.",
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(1)

        await query.edit_message_text(
            "⏱️ *Gestione dei tempi*",
            reply_markup=build_timing_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_offers_count":
        current = get_user_offers_per_cycle(user_id)
        keyboard = []
        row = []

        for val in [1, 2, 3, 4, 5, 6, 8, 10]:
            label = f"{val} offerta" if val == 1 else f"{val} offerte"
            if val == current:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"offers_count_{val}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Tempi", callback_data="timing_menu")])

        await query.edit_message_text(
            "🔢 *Quante offerte vuoi pubblicare per ogni ciclo?*\n\n"
            "Esempio: se imposti 3 e l’intervallo è 30 minuti, il bot può pubblicare fino a 3 offerte ogni 30 minuti.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("offers_count_"):
        value = int(data.rsplit("_", 1)[1])
        set_user_offers_per_cycle(user_id, value)

        await query.edit_message_text(
            f"✅ Numero offerte aggiornato: *{value}* per categoria a ogni ciclo.",
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(1)

        await query.edit_message_text(
            "⏱️ *Gestione dei tempi*",
            reply_markup=build_timing_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_days":
        current = get_user_days_limit(user_id)
        keyboard = []
        row = []

        for d in range(1, 8):
            label = f"{d} giorno" if d == 1 else f"{d} giorni"
            if d == current:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"days_{d}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Tempi", callback_data="timing_menu")])

        await query.edit_message_text(
            "🕒 *Quanti giorni devono passare prima di ripubblicare un prodotto?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("days_"):
        value = int(data.split("_")[1])
        set_user_days_limit(user_id, value)

        await query.edit_message_text(
            f"✅ Attesa impostata a *{value}* giorno/i prima della ripubblicazione.",
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(1)

        await query.edit_message_text(
            "⏱️ *Gestione dei tempi*",
            reply_markup=build_timing_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_buffer_clear":
        current_days = get_user_buffer_clear_days(user_id)
        keyboard = []
        row = []

        for d in [0, 1, 2, 3, 5, 7]:
            label = "🚫 Disattiva" if d == 0 else f"Ogni {d} giorni"
            if d == current_days:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"buffer_clear_{d}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Tempi", callback_data="timing_menu")])

        await query.edit_message_text(
            "🧹 *Pulizia automatica del buffer*\n\n"
            "Imposta ogni quanti giorni il bot deve svuotare automaticamente i buffer.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("buffer_clear_"):
        days = int(data.split("_")[2])
        set_user_buffer_clear_days(user_id, days)

        msg = "🚫 Pulizia automatica disattivata." if days == 0 else f"✅ Pulizia automatica impostata ogni *{days}* giorni."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(1)

        await query.edit_message_text(
            "⏱️ *Gestione dei tempi*",
            reply_markup=build_timing_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_days_hours":
        keyboard = [
            [InlineKeyboardButton("🗓️ Giorni Attivi", callback_data="edit_active_days")],
            [InlineKeyboardButton("⏰ Orario Attivo", callback_data="edit_active_hours")],
            [InlineKeyboardButton("🔙 Torna al menu Tempi", callback_data="timing_menu")]
        ]
        await query.edit_message_text(
            "📅 *Configura i giorni e gli orari in cui il bot pubblica automaticamente.*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "edit_active_days":
        from src.configs.schedule_config import get_user_schedule

        current = get_user_schedule(user_id).get("days", [])
        days = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        keyboard = []
        row = []

        for i, day in enumerate(days):
            label = f"{'✅ ' if day in current else '⬜ '}{day}"
            row.append(InlineKeyboardButton(label, callback_data=f"toggle_day_{day}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Giorni/Orari", callback_data="set_days_hours")])

        await query.edit_message_text(
            "🗓️ *Seleziona i giorni attivi:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("toggle_day_"):
        from src.configs.schedule_config import get_user_schedule, set_user_schedule

        day = data.split("_")[-1]
        schedule = get_user_schedule(user_id)
        schedule.setdefault("days", [])

        if day in schedule["days"]:
            schedule["days"].remove(day)
        else:
            schedule["days"].append(day)

        set_user_schedule(user_id, schedule)

        days = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        keyboard = []
        row = []

        for i, d in enumerate(days):
            label = f"{'✅ ' if d in schedule['days'] else '⬜ '}{d}"
            row.append(InlineKeyboardButton(label, callback_data=f"toggle_day_{d}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna al menu Giorni/Orari", callback_data="set_days_hours")])

        await query.edit_message_text(
            "🗓️ *Giorni attivi aggiornati*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "edit_active_hours":
        from src.configs.schedule_config import get_user_schedule

        schedule = get_user_schedule(user_id)
        current_start = schedule.get("time_range", {}).get("start", "08:00")
        current_end = schedule.get("time_range", {}).get("end", "22:00")

        keyboard = [
            [InlineKeyboardButton(f"🟢 Inizio: {current_start}", callback_data="set_start_time")],
            [InlineKeyboardButton(f"🔴 Fine: {current_end}", callback_data="set_end_time")],
            [InlineKeyboardButton("🔙 Torna a Giorni/Orari", callback_data="set_days_hours")]
        ]

        await query.edit_message_text(
            (
                "⏰ *Configura la fascia oraria attiva*\n\n"
                f"• Inizio: `{current_start}`\n"
                f"• Fine: `{current_end}`"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "set_start_time":
        context.user_data["awaiting_start_time"] = True
        await query.edit_message_text(
            "✏️ Inviami l’orario di *inizio* in formato `HH:MM`\nEsempio: `08:00`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_end_time":
        context.user_data["awaiting_end_time"] = True
        await query.edit_message_text(
            "✏️ Inviami l’orario di *fine* in formato `HH:MM`\nEsempio: `22:00`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FILTRI
    # -------------------------------------------------------------------------
    if data == "filters":
        current_discount = get_user_min_discount(user_id)
        keyboard = []
        row = []

        for val in range(5, 101, 5):
            label = f"≥ {val}%"
            if val == current_discount:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"discount_{val}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")])

        await query.edit_message_text(
            "🌟 *Scegli il filtro minimo di sconto:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("discount_"):
        value = int(data.split("_", 1)[1])
        set_user_min_discount(user_id, value)
        await query.answer(f"🎯 Filtro impostato a ≥{value}%")

        users = load_user_data()
        categories = users.get(str(user_id), {}).get("categories", [])

        for cat_code in categories:
            try:
                delete_buffer_file(user_id, cat_code)
                logger.info(f"[FILTER] Buffer cancellato: {user_id}_{cat_code}")
            except Exception:
                logger.exception(f"[FILTER] Errore cancellazione buffer {user_id}_{cat_code}")

        await query.edit_message_text(
            f"✅ Filtro aggiornato: solo offerte con sconto *≥ {value}%*",
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(1)

        await query.edit_message_text(
            "⚙️ *Impostazioni aggiornate*",
            reply_markup=build_settings_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # SHOW CATEGORIES
    # -------------------------------------------------------------------------
    if data == "show_categories":
        reply_markup = build_category_keyboard(user_id)
        try:
            await query.edit_message_text(
                text="📂 *Seleziona le categorie che desideri monitorare:*",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"[CATEGORIES] edit_message_text fallita: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="📂 *Seleziona le categorie che desideri monitorare:*",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # -------------------------------------------------------------------------
    # MENU PRINCIPALE
    # -------------------------------------------------------------------------
    if data == "back_to_menu":
        await query.edit_message_text(
            build_welcome_text(user.first_name),
            reply_markup=build_main_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # LICENZA
    # -------------------------------------------------------------------------
    if data == "license_menu":
        user_info = get_user_info(user_id)

        stato = "❌ Nessuna licenza attiva.\nContattami su @Gianluca85"
        if user_info.get("has_license"):
            exp = user_info.get("license_expires", "?")
            stato = f"✅ Licenza attiva\nValida fino al *{exp}*"

        keyboard = [
            [InlineKeyboardButton("🏷️ Imposta Tag Affiliato", callback_data="set_tag")],
            [InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")]
        ]

        await query.edit_message_text(
            f"🎟️ *Licenza & Affiliazione*\n\n{stato}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "set_tag":
        user_info = get_user_info(user_id)

        if not license_is_valid(user_info):
            await query.edit_message_text(
                f"🚫 La tua licenza risulta scaduta il *{user_info.get('license_expires', '?')}*.\n"
                "Devi rinnovarla prima di impostare un tag affiliato.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        context.user_data["awaiting_tag"] = True
        await query.edit_message_text(
            "✏️ Inviami ora il tuo *Tag Affiliato Amazon*\n\nEsempio: `ilmionome-21`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # LINK MANUALE
    # -------------------------------------------------------------------------
    if data == "manual_link":
        context.user_data["awaiting_manual_link"] = True
        context.user_data["manual_prompt_msg_id"] = query.message.message_id

        await query.edit_message_text(
            "📥 Inviami un link Amazon per creare un post manuale.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # TOGGLE CATEGORIA
    # -------------------------------------------------------------------------
    selected_before = get_user_categories(user_id)
    label = next((lbl for lbl, code in CATEGORIES if code == data), "Categoria")

    if data in [code for _, code in CATEGORIES]:
        if data in selected_before:
            toggle_user_category(user_id, data)

            await query.edit_message_text(
                f"❌ Hai disattivato: *{label}*",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(2)
        else:
            toggle_user_category(user_id, data)

            try:
                delete_buffer_file(user_id, data)
            except Exception:
                pass

            await query.edit_message_text(
                f"✅ Hai attivato: *{label}*\n\n🔎 Scansione in corso... puoi continuare a usare il bot.",
                parse_mode=ParseMode.MARKDOWN
            )

            asyncio.create_task(async_refill_and_notify(user_id, data))
            await asyncio.sleep(2)

        await query.edit_message_text(
            "📲 Scegli cosa vuoi fare:",
            reply_markup=build_main_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return


# -----------------------------------------------------------------------------
# ERROR HANDLER
# -----------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("❌ Eccezione non gestita:", exc_info=context.error)


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN mancante. Copia .env.example in .env e inserisci il token del bot.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_error_handler(error_handler)

    # Comandi
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("testofferta", test_offerta))
    app.add_handler(CommandHandler("attivalicenza", attivalicenza_cmd))
    app.add_handler(CommandHandler("adminstats", admin_stats_cmd))
    app.add_handler(CommandHandler("radar", radar_command))
    app.add_handler(CommandHandler("track", track_cmd))
    app.add_handler(CommandHandler("untrack", untrack_cmd))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))

    # Callback specializzati prima del generico
    app.add_handler(CallbackQueryHandler(track_button_handler, pattern=r"^track_"))
    app.add_handler(CallbackQueryHandler(show_instagram_menu, pattern=r"^connect_instagram$"))
    app.add_handler(
        CallbackQueryHandler(
            handle_instagram_callback,
            pattern=r"^(unlink_instagram|change_instagram)$"
        )
    )

    # Callback generico menu/categorie
    app.add_handler(CallbackQueryHandler(category_selected))

    # Un solo handler per il testo
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Scheduler in thread separato
    def scheduler_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logger.info("[SCHEDULER] Avvio in corso...")
            loop.run_until_complete(start_scheduler(app))
        except Exception as e:
            logger.exception(f"[SCHEDULER] Errore in fase di avvio: {e}")
            with open("scheduler_error.log", "a", encoding="utf-8") as f:
                f.write("Errore nel thread scheduler:\n")
                f.write(traceback.format_exc())
        finally:
            loop.close()

    Thread(target=scheduler_thread, daemon=True).start()

    logger.info("🤖 Bot avviato. Inizio polling...")
    app.run_polling(drop_pending_updates=True)
