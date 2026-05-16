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
from telegram.helpers import escape_markdown
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
    get_user_category_scrolls,
    set_user_category_scrolls,
    get_user_category_pages,
    set_user_category_pages,
    get_user_telegram_source_channels,
    add_user_telegram_source_channel,
    remove_user_telegram_source_channel,
    get_user_telegram_source_limit,
    set_user_telegram_source_limit,
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
    ("Fonti Telegram 📥", "cat_telegram_sources"),
]

INFO_BUTTON = ("ℹ️ Info", "info")
SETTINGS_BUTTON = ("⚙️ Impostazioni", "settings")
CATEGORY_BUTTON = ("📂 Categorie", "show_categories")
FUNCTIONS_BUTTON = ("🛠️ Funzioni", "funzioni_menu")
REFRESH_BUTTON = ("🔄 Aggiorna", "back_to_menu")

CATEGORY_LABELS = {code: label for label, code in CATEGORIES}

# Scansioni manuali Fonti Telegram in background.
# Serve per non bloccare la dashboard mentre Telethon/PA-API/Selenium lavorano.
_ACTIVE_TELEGRAM_SOURCE_SCANS: set[int] = set()


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
        [
            InlineKeyboardButton(CATEGORY_BUTTON[0], callback_data=CATEGORY_BUTTON[1]),
            InlineKeyboardButton(SETTINGS_BUTTON[0], callback_data=SETTINGS_BUTTON[1]),
        ],
        [
            InlineKeyboardButton("🧺 Buffer", callback_data="buffer_dashboard"),
            InlineKeyboardButton(FUNCTIONS_BUTTON[0], callback_data=FUNCTIONS_BUTTON[1]),
        ],
        [
            InlineKeyboardButton(INFO_BUTTON[0], callback_data=INFO_BUTTON[1]),
            InlineKeyboardButton(REFRESH_BUTTON[0], callback_data=REFRESH_BUTTON[1]),
        ],
    ])


def build_connect_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Telegram", callback_data="connect_telegram")],
        [InlineKeyboardButton("📘 Facebook", callback_data="connect_facebook")],
        [InlineKeyboardButton("📷 Instagram", callback_data="connect_instagram")],
        [InlineKeyboardButton("🔙 Impostazioni", callback_data="settings")],
    ])


def build_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔽 Filtri", callback_data="filters"),
            InlineKeyboardButton("⏱️ Tempi", callback_data="timing_menu"),
        ],
        [
            InlineKeyboardButton("🔗 Canali", callback_data="connect_menu"),
            InlineKeyboardButton("🎟️ Licenza", callback_data="license_menu"),
        ],
        [InlineKeyboardButton("🔃 Scroll categorie", callback_data="category_scrolls_menu")],
        [InlineKeyboardButton("📄 Pagine categorie", callback_data="category_pages_menu")],
        [InlineKeyboardButton("🔙 Dashboard", callback_data="back_to_menu")],
    ])


def build_admin_license_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 30 giorni", callback_data="admin_license_days_30"),
            InlineKeyboardButton("✅ 90 giorni", callback_data="admin_license_days_90"),
        ],
        [
            InlineKeyboardButton("✅ 180 giorni", callback_data="admin_license_days_180"),
            InlineKeyboardButton("✅ 365 giorni", callback_data="admin_license_days_365"),
        ],
        [InlineKeyboardButton("✏️ Durata manuale", callback_data="admin_license_custom_days")],
        [InlineKeyboardButton("📋 Utenti registrati", callback_data="admin_license_users")],
        [InlineKeyboardButton("🔙 Torna a Licenza", callback_data="license_menu")],
    ])


def build_admin_license_text() -> str:
    return (
        "👑 *Gestione Licenze Admin*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Da qui puoi attivare o rinnovare una licenza senza usare comandi manuali.\n\n"
        "1️⃣ Scegli la durata\n"
        "2️⃣ Invia il Telegram ID dell’utente\n"
        "3️⃣ Il bot salva automaticamente la nuova scadenza\n\n"
        "Puoi usare anche il comando:\n"
        "`/attivalicenza <user_id> <giorni>`"
    )


def build_functions_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Pubblica da link Amazon", callback_data="manual_link")],
        [InlineKeyboardButton("📥 Fonti Telegram", callback_data="telegram_sources_menu")],
        [InlineKeyboardButton("🔙 Dashboard", callback_data="back_to_menu")],
    ])




def build_telegram_sources_menu(user_id: int) -> InlineKeyboardMarkup:
    sources = get_user_telegram_source_channels(user_id)
    keyboard = [
        [InlineKeyboardButton("➕ Aggiungi canale sorgente", callback_data="telegram_source_add")],
        [InlineKeyboardButton("🔍 Scansiona ora", callback_data="telegram_source_scan_all")],
        [InlineKeyboardButton("⚙️ Limite lettura", callback_data="telegram_source_limit_menu")],
        [InlineKeyboardButton("📘 Guida funzione", callback_data="telegram_source_guide")],
    ]
    for ch in sources[:10]:
        clean = ch.lstrip("@")
        keyboard.append([InlineKeyboardButton(f"❌ Rimuovi {ch}", callback_data=f"telegram_source_remove_{clean}")])
    keyboard.append([InlineKeyboardButton("🔙 Funzioni", callback_data="funzioni_menu")])
    return InlineKeyboardMarkup(keyboard)


def build_telegram_source_limit_menu(user_id: int) -> InlineKeyboardMarkup:
    current = get_user_telegram_source_limit(user_id)
    options = [10, 20, 30, 50, 75, 100, 150, 200]
    rows = []
    row = []
    for value in options:
        label = f"✅ {value}" if value == current else str(value)
        row.append(InlineKeyboardButton(label, callback_data=f"telegram_source_limit_{value}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("📘 Guida limite", callback_data="telegram_source_limit_guide")])
    rows.append([InlineKeyboardButton("🔙 Fonti Telegram", callback_data="telegram_sources_menu")])
    return InlineKeyboardMarkup(rows)


def build_telegram_source_limit_text(user_id: int) -> str:
    current = get_user_telegram_source_limit(user_id)
    return (
        "⚙️ *Limite lettura Fonti Telegram*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Valore attuale: *{current} messaggi per fonte*\n\n"
        "Questo valore indica quanti messaggi recenti leggere da ogni canale sorgente.\n\n"
        "📌 *Consiglio pratico*\n"
        "• 10-30 = leggero e veloce\n"
        "• 50 = consigliato per uso normale\n"
        "• 75-100 = scansione più profonda\n"
        "• 150-200 = pesante, utile solo se il canale pubblica molto\n\n"
        "Più aumenti il limite, più offerte puoi trovare, ma la scansione sarà più lenta."
    )


def build_telegram_source_limit_guide_text() -> str:
    return (
        "📘 *Guida limite lettura Telegram*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Il limite non significa ‘numero offerte da importare’.\n"
        "Significa quanti messaggi il bot legge dal canale.\n\n"
        "Esempio:\n"
        "• Limite 30 = legge gli ultimi 30 messaggi\n"
        "• Se solo 1 messaggio contiene link Amazon, importa 1 candidato\n\n"
        "🔍 *Perché può trovare poche offerte?*\n"
        "• post senza link Amazon\n"
        "• link dentro redirect strani\n"
        "• ASIN già importati o pubblicati\n"
        "• prodotti senza prezzo valido\n"
        "• post duplicati dello stesso prodotto\n\n"
        "🚀 *Impostazione consigliata*\n"
        "Usa 50 per l’automatico. Usa 100/150 solo per scansioni manuali più profonde."
    )



def _format_source_stat_time(ts: object) -> str:
    try:
        from datetime import datetime
        value = int(ts or 0)
        if value <= 0:
            return "mai"
        return datetime.fromtimestamp(value).strftime("%d/%m %H:%M")
    except Exception:
        return "mai"


def _telegram_sources_stats_lines(user_id: int) -> str:
    sources = get_user_telegram_source_channels(user_id)
    if not sources:
        return "Nessun canale sorgente configurato."

    try:
        from src.telegram_sources.source_store import get_source_stats
        stats_map = get_source_stats(user_id)
    except Exception:
        stats_map = {}

    lines = []
    for ch in sources:
        stat = stats_map.get(ch, {}) if isinstance(stats_map, dict) else {}
        if not stat:
            lines.append(f"• `{ch}` → mai scansionato")
            continue
        links = int(stat.get("found_links", 0) or 0)
        asins = int(stat.get("found_asins", 0) or 0)
        added = int(stat.get("added_products", 0) or 0)
        skipped = int(stat.get("skipped_invalid", 0) or 0)
        errors = int(stat.get("errors", 0) or 0)
        when = _format_source_stat_time(stat.get("updated_at"))
        lines.append(
            f"• `{ch}` → 🔗 {links} | ASIN {asins} | ✅ {added} | 🗑️ {skipped} | ⚠️ {errors} | {when}"
        )
    return "\n".join(lines)

def build_telegram_sources_text(user_id: int) -> str:
    limit = get_user_telegram_source_limit(user_id)
    lines = _telegram_sources_stats_lines(user_id)
    return (
        "📥 *Fonti Telegram*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Da qui puoi inserire canali Telegram pubblici da cui prelevare segnalazioni offerte.\n\n"
        "Il bot legge gli ultimi post del canale, cerca link Amazon, recupera l’ASIN e ricostruisce l’offerta con i tuoi dati e i tuoi filtri.\n\n"
        f"📌 Messaggi letti per canale: *{limit}*\n"
        f"📦 Buffer destinazione: *Fonti Telegram*\n\n"
        "📊 *Statistiche ultima scansione*\n"
        "Legenda: 🔗 link trovati | ASIN recuperati | ✅ aggiunti | 🗑️ scartati | ⚠️ errori | ultimo controllo\n"
        f"{lines}\n\n"
        "Nota: funziona con canali pubblici leggibili da `t.me/s/nomecanale`, oppure con Telethon se abilitato."
    )


def build_telegram_sources_guide_text() -> str:
    return (
        "📘 *Guida Fonti Telegram*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Questa funzione usa Telegram come fonte di segnalazione offerte.\n\n"
        "✅ *Modalità base*\n"
        "• Inserisci un canale pubblico, esempio `@nomecanale`\n"
        "• Il bot legge la preview pubblica `t.me/s/nomecanale`\n"
        "• Estrae link Amazon, ASIN, prezzo e foto quando disponibili\n\n"
        "🚀 *Modalità avanzata Telethon*\n"
        "Se nel `.env` abiliti `TELETHON_ENABLED=true`, il bot usa un account Telegram reale.\n"
        "Così può leggere meglio:\n"
        "• bottoni inline con link nascosti\n"
        "• foto reali dei post\n"
        "• canali/gruppi dove l’account è membro\n"
        "• messaggi ricevuti da altri bot, se l’account ha quella chat\n\n"
        "🔐 *Prima configurazione Telethon*\n"
        "1. Vai su `my.telegram.org`\n"
        "2. Crea una app e copia `API_ID` e `API_HASH`\n"
        "3. Mettili nel `.env`\n"
        "4. Esegui `python telethon_login.py`\n"
        "5. Imposta `TELETHON_ENABLED=true` e riavvia\n\n"
        "🛡️ *Importante*\n"
        "Il bot non copia il testo originale degli altri canali. Usa link/ASIN/prezzo/foto come segnale e ricrea il post con il tuo formato, il tuo tag affiliato e i tuoi filtri.\n\n"
        "📊 *Statistiche*\n"
        "Nel menu Fonti Telegram vedi, per ogni canale, quanti link/ASIN sono stati trovati, quante offerte sono entrate nel buffer e quante sono state scartate.\n\n"
        "📦 Buffer usato: *Fonti Telegram 📥*"
    )

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


def build_category_scrolls_menu(user_id: int) -> InlineKeyboardMarkup:
    current = get_user_category_scrolls(user_id)
    values = [3, 5, 8, 10, 12, 15, 20, 25, 30]
    keyboard = []
    row = []

    for value in values:
        label = f"{value} scroll"
        if value == current:
            label = f"✅ {label}"
        row.append(InlineKeyboardButton(label, callback_data=f"category_scrolls_{value}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("📘 Guida scroll", callback_data="category_scrolls_guide")])
    keyboard.append([InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


def build_category_scrolls_text(user_id: int) -> str:
    current = get_user_category_scrolls(user_id)
    return (
        "🔃 *Scroll categorie*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Da qui imposti quanti scroll deve fare Selenium quando scansiona le pagine categoria Amazon.\n\n"
        f"📌 Valore attuale: *{current} scroll*\n\n"
        "Questo valore viene usato da tutte le categorie in modo uniforme, così non hai più categorie con scroll diversi scritti nei singoli file.\n\n"
        "Consiglio pratico:\n"
        "• *3-5* = test veloce\n"
        "• *8-12* = uso normale consigliato\n"
        "• *15-30* = scansione profonda, più lenta e più pesante\n\n"
        "Dopo aver cambiato il valore, il prossimo refill userà la nuova impostazione."
    )


def build_category_scrolls_guide_text(user_id: int) -> str:
    current = get_user_category_scrolls(user_id)
    return (
        "📘 *Guida Scroll Categorie*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Valore attuale: *{current} scroll*\n\n"
        "🔍 *Cosa sono gli scroll?*\n"
        "Quando il bot apre una pagina Amazon, molti prodotti vengono caricati solo scorrendo la pagina. "
        "Più scroll significa più prodotti trovati, ma anche scansioni più lente.\n\n"
        "⚡ *Quando usare pochi scroll*\n"
        "Usa 3-5 scroll se stai testando il bot, se il PC è sotto carico o se vuoi refill più rapidi.\n\n"
        "✅ *Valore consigliato*\n"
        "Per uso normale usa 8-12 scroll. È il compromesso migliore tra velocità e quantità di prodotti.\n\n"
        "🧲 *Quando usare tanti scroll*\n"
        "Usa 15-30 scroll solo se vuoi riempire meglio i buffer e puoi aspettare di più. "
        "Valori alti possono aumentare blocchi, CAPTCHA o lentezza di Selenium.\n\n"
        "📌 *Nota importante*\n"
        "Questa impostazione non pubblica più offerte da sola: decide solo quanto in profondità cercare i prodotti durante il refill."
    )


def build_category_pages_menu(user_id: int) -> InlineKeyboardMarkup:
    current = get_user_category_pages(user_id)
    values = [1, 2, 3, 4, 5, 7, 10]
    keyboard = []
    row = []

    for value in values:
        label = f"{value} pagina" if value == 1 else f"{value} pagine"
        if value == current:
            label = f"✅ {label}"
        row.append(InlineKeyboardButton(label, callback_data=f"category_pages_{value}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("📘 Guida pagine", callback_data="category_pages_guide")])
    keyboard.append([InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


def build_category_pages_text(user_id: int) -> str:
    current = get_user_category_pages(user_id)
    scrolls = get_user_category_scrolls(user_id)
    return (
        "📄 *Pagine categorie*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Da qui imposti quante pagine Amazon deve aprire per ogni categoria durante il refill.\n\n"
        f"📌 Valore attuale: *{current} pagine*\n"
        f"🔃 Scroll per pagina: *{scrolls}*\n\n"
        "Prima il bot scorre la pagina corrente, poi prova a cliccare/aprire *pagina successiva*. "
        "Questo aiuta quando Amazon non carica più prodotti con il solo scroll.\n\n"
        "Consiglio pratico:\n"
        "• *1 pagina* = test veloce\n"
        "• *2-3 pagine* = uso normale consigliato\n"
        "• *4-5 pagine* = più offerte, più lento\n"
        "• *7-10 pagine* = scansione profonda, rischio CAPTCHA più alto\n\n"
        "Dopo aver cambiato il valore, cancello i buffer attivi così il prossimo refill userà subito la nuova profondità."
    )


def build_category_pages_guide_text(user_id: int) -> str:
    current = get_user_category_pages(user_id)
    scrolls = get_user_category_scrolls(user_id)
    return (
        "📘 *Guida Pagine Categorie*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Valore attuale: *{current} pagine*\n"
        f"Scroll per pagina: *{scrolls}*\n\n"
        "🔍 *Perché serve?*\n"
        "Su alcune categorie Amazon lo scroll non basta: dopo aver caricato tutta la prima pagina, bisogna passare alla pagina successiva per trovare altre offerte.\n\n"
        "✅ *Impostazione consigliata*\n"
        "Usa *2 o 3 pagine* per avere più candidati senza appesantire troppo Chrome/Selenium.\n\n"
        "⚠️ *Attenzione*\n"
        "Più pagine significano più tempo, più richieste ad Amazon e più rischio CAPTCHA. "
        "Se noti rallentamenti o blocchi, torna a 2 pagine e abbassa gli scroll.\n\n"
        "📌 *Formula pratica*\n"
        "Lavoro totale ≈ pagine categorie × scroll categorie.\n"
        "Esempio: 3 pagine × 8 scroll = 24 passaggi di scansione per categoria."
    )


def _count_buffer_for_category(user_id: int, category_code: str) -> int:
    try:
        from src.buffer.buffer_manager import count_products_in_buffer
        return int(count_products_in_buffer(user_id, category_code))
    except Exception:
        return 0


def _count_all_buffers(user_id: int, categories: list[str]) -> tuple[int, dict[str, int]]:
    per_category: dict[str, int] = {}
    total = 0
    for code in categories:
        count = _count_buffer_for_category(user_id, code)
        per_category[code] = count
        total += count
    return total, per_category


def _short_category_label(category_code: str) -> str:
    label = CATEGORY_LABELS.get(category_code, category_code)
    return label.replace("✅ ", "").strip()


def _format_license_status(user_info: dict) -> str:
    if license_is_valid(user_info):
        return f"✅ attiva fino al {user_info.get('license_expires', '?')}"
    if user_info.get("has_license"):
        return "⚠️ scaduta"
    return "❌ non attiva"


def build_welcome_text(first_name: str, user_id: int | None = None) -> str:
    now = datetime.now().strftime("%H:%M")
    safe_first_name = escape_markdown(first_name or "Admin", version=1)

    active_categories: list[str] = []
    user_info: dict = {}
    total_buffer = 0
    channels_count = 0
    min_discount = 20
    post_interval = 15
    offers_per_cycle = 1
    days_delay = 2
    buffer_clear_days = 0
    license_status = "❌ non attiva"

    if user_id is not None:
        try:
            active_categories = get_user_categories(user_id)
            user_info = get_user_info(user_id)
            total_buffer, _ = _count_all_buffers(user_id, active_categories)
            channels_count = len(user_info.get("telegram_channels", []) or [])
            min_discount = get_user_min_discount(user_id)
            post_interval = get_user_post_interval(user_id)
            offers_per_cycle = get_user_offers_per_cycle(user_id)
            days_delay = get_user_days_limit(user_id)
            buffer_clear_days = get_user_buffer_clear_days(user_id)
            license_status = _format_license_status(user_info)
        except Exception:
            logger.exception("[DASHBOARD] Errore costruzione dashboard")

    categories_preview = "nessuna"
    if active_categories:
        labels = [_short_category_label(code) for code in active_categories[:4]]
        categories_preview = ", ".join(labels)
        if len(active_categories) > 4:
            categories_preview += f" +{len(active_categories) - 4}"

    channel_status = f"{channels_count} collegati" if channels_count else "nessun canale"
    clear_status = f"ogni {buffer_clear_days} giorni" if buffer_clear_days else "manuale"

    return (
        "🛒 *AMAZON OFFERS BOT*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👋 Ciao *{safe_first_name}*\n"
        f"🟢 Stato: *pronto*\n"
        f"🕒 Aggiornato: *{now}*\n\n"
        "📊 *Panoramica*\n"
        f"• Categorie attive: *{len(active_categories)}*\n"
        f"• Prodotti in buffer: *{total_buffer}*\n"
        f"• Canali: *{channel_status}*\n"
        f"• Licenza: *{license_status}*\n\n"
        "⚙️ *Configurazione*\n"
        f"• Offerte per categoria: *{offers_per_cycle}*\n"
        f"• Intervallo post: *{post_interval} min*\n"
        f"• Sconto minimo: *{min_discount}%*\n"
        f"• Ripubblicazione: *dopo {days_delay} giorni*\n"
        f"• Pulizia buffer: *{clear_status}*\n\n"
        "📦 *Categorie*\n"
        f"• {categories_preview}\n\n"
        "👇 Usa i pulsanti qui sotto per gestire il bot."
    )


def build_category_text(user_id: int) -> str:
    selected = get_user_categories(user_id)
    total_buffer, per_category = _count_all_buffers(user_id, selected)

    if selected:
        selected_lines = []
        for code in selected:
            selected_lines.append(f"• {_short_category_label(code)}: *{per_category.get(code, 0)}* offerte")
        selected_text = "\n".join(selected_lines)
    else:
        selected_text = "• Nessuna categoria attiva"

    return (
        "📂 *Categorie monitorate*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Seleziona o rimuovi le categorie da monitorare.\n\n"
        f"✅ Attive: *{len(selected)}*\n"
        f"🧺 Buffer totale: *{total_buffer}* offerte\n\n"
        f"{selected_text}\n\n"
        "Tocca una categoria per attivarla o disattivarla."
    )


def build_settings_text(user_id: int) -> str:
    user_info = get_user_info(user_id)
    channels_count = len(user_info.get("telegram_channels", []) or [])
    return (
        "⚙️ *Impostazioni*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Gestisci pubblicazione, canali e affiliazione.\n\n"
        f"🔽 Sconto minimo: *{get_user_min_discount(user_id)}%*\n"
        f"⏱️ Intervallo: *{get_user_post_interval(user_id)} min*\n"
        f"🔢 Offerte per categoria: *{get_user_offers_per_cycle(user_id)}*\n"
        f"🔃 Scroll categorie: *{get_user_category_scrolls(user_id)}*\n"
        f"📄 Pagine categorie: *{get_user_category_pages(user_id)}*\n"
        f"📣 Canali collegati: *{channels_count}*\n"
        f"🎟️ Licenza: *{_format_license_status(user_info)}*\n\n"
        "Scegli una sezione da configurare."
    )


def build_functions_text() -> str:
    return (
        "🛠️ *Funzioni rapide*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Strumenti manuali per lavorare più velocemente.\n\n"
        "🔗 *Pubblica da link Amazon*\n"
        "Incolla un link prodotto e il bot prepara post, immagine e pulsante.\n\n"
        "Seleziona una funzione."
    )


def build_buffer_dashboard_text(user_id: int) -> str:
    selected = get_user_categories(user_id)
    total_buffer, per_category = _count_all_buffers(user_id, selected)

    if selected:
        lines = []
        for code in selected:
            count = per_category.get(code, 0)
            status = "🟢" if count >= 10 else "🟡" if count > 0 else "🔴"
            lines.append(f"{status} {_short_category_label(code)}: *{count}*")
        body = "\n".join(lines)
    else:
        body = "Nessuna categoria attiva."

    return (
        "🧺 *Stato Buffer*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 Totale offerte pronte: *{total_buffer}*\n"
        f"📂 Categorie attive: *{len(selected)}*\n\n"
        f"{body}\n\n"
        "Legenda: 🟢 ok · 🟡 basso · 🔴 vuoto"
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
        "awaiting_admin_license_user_id",
        "awaiting_admin_license_custom_days",
        "admin_license_days",
        "admin_license_prompt_msg_id",
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
        # La categoria Fonti Telegram non ha uno scraper Amazon tradizionale.
        # Va riempita scansionando i canali salvati in telegram_sources.json / user_data.json.
        if category_code == "cat_telegram_sources":
            from src.telegram_sources.importer import import_channel_offers_to_buffer

            sources = get_user_telegram_source_channels(user_id)
            if not sources:
                logger.info(
                    "[REFILL] Fonti Telegram: nessuna fonte configurata per user=%s",
                    user_id,
                )
                return

            limit = get_user_telegram_source_limit(user_id)
            total_links = 0
            total_asins = 0
            total_added = 0
            total_errors = 0

            logger.info(
                "[REFILL] Fonti Telegram: scansiono %s fonti per user=%s limit=%s",
                len(sources),
                user_id,
                limit,
            )

            for source in sources:
                result = await asyncio.to_thread(
                    import_channel_offers_to_buffer,
                    user_id,
                    source,
                    limit,
                )
                total_links += getattr(result, "found_links", 0)
                total_asins += getattr(result, "found_asins", 0)
                total_added += getattr(result, "added_products", 0)
                total_errors += getattr(result, "errors", 0)

            logger.info(
                "[REFILL] Fonti Telegram completate per user=%s | fonti=%s link=%s asin=%s aggiunti=%s errori=%s",
                user_id,
                len(sources),
                total_links,
                total_asins,
                total_added,
                total_errors,
            )
            return

        if category_code not in scrapers:
            logger.warning(
                "[REFILL] Categoria non gestita: user=%s category=%s",
                user_id,
                category_code,
            )
            return

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
        build_welcome_text(user.first_name, user.id),
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
    # ADMIN: ATTIVAZIONE LICENZA DA PANNELLO
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_admin_license_custom_days"):
        if not is_admin(user_id):
            context.user_data.pop("awaiting_admin_license_custom_days", None)
            await update.message.reply_text("🚫 Operazione riservata all’amministratore.")
            return

        try:
            days = int(text.strip())
            if days <= 0 or days > 3650:
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Durata non valida. Invia un numero di giorni, esempio: `30`", parse_mode=ParseMode.MARKDOWN)
            return

        context.user_data["awaiting_admin_license_custom_days"] = False
        context.user_data["awaiting_admin_license_user_id"] = True
        context.user_data["admin_license_days"] = days

        try:
            await update.message.delete()
        except Exception:
            pass

        prompt_id = context.user_data.get("admin_license_prompt_msg_id")
        text_msg = (
            "👑 *Gestione Licenze Admin*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"✅ Durata scelta: *{days} giorni*\n\n"
            "Ora inviami il *Telegram ID* dell’utente da attivare.\n\n"
            "Esempio: `1271567510`"
        )
        if prompt_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=prompt_id,
                    text=text_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annulla", callback_data="admin_license_menu")]])
                )
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=text_msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id=chat_id, text=text_msg, parse_mode=ParseMode.MARKDOWN)
        return

    if context.user_data.get("awaiting_admin_license_user_id"):
        from src.utils.license_manager import attiva_licenza

        if not is_admin(user_id):
            context.user_data.pop("awaiting_admin_license_user_id", None)
            await update.message.reply_text("🚫 Operazione riservata all’amministratore.")
            return

        days = int(context.user_data.get("admin_license_days", 30))
        try:
            target_user_id = int(text.strip())
        except Exception:
            await update.message.reply_text("❌ Telegram ID non valido. Invia solo numeri, esempio: `1271567510`", parse_mode=ParseMode.MARKDOWN)
            return

        try:
            attiva_licenza(target_user_id, days)
            data = load_user_data()
            exp = data.get(str(target_user_id), {}).get("license_expires", "?")
            notified = await notify_user_license_activated(context.bot, target_user_id, days, exp)

            context.user_data.pop("awaiting_admin_license_user_id", None)
            context.user_data.pop("admin_license_days", None)

            try:
                await update.message.delete()
            except Exception:
                pass

            result_text = (
                "✅ *Licenza attivata*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"👤 Utente: `{target_user_id}`\n"
                f"⏳ Durata: *{days} giorni*\n"
                f"📅 Scadenza: *{exp}*\n"
                f"📩 Notifica utente: *{'inviata' if notified else 'non inviata'}*\n\n"
                "Puoi attivare un’altra licenza oppure tornare al pannello."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Attiva altra licenza", callback_data="admin_license_menu")],
                [InlineKeyboardButton("🔙 Licenza & Affiliazione", callback_data="license_menu")],
            ])

            prompt_id = context.user_data.get("admin_license_prompt_msg_id")
            if prompt_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=prompt_id,
                        text=result_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard
                    )
                except Exception:
                    await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

        except Exception as e:
            logger.exception("[ADMIN_LICENSE] Errore attivazione da pannello")
            await update.message.reply_text(f"⚠️ Errore attivazione licenza: `{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # FONTI TELEGRAM: AGGIUNGI CANALE SORGENTE
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_telegram_source_channel"):
        from src.telegram_sources.importer import normalize_channel_name

        clean = normalize_channel_name(text)
        if not clean:
            await update.message.reply_text(
                "❌ Canale non valido. Inviami un canale pubblico, esempio: `@nomecanale` oppure `https://t.me/nomecanale`.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            add_user_telegram_source_channel(user_id, clean)
            context.user_data["awaiting_telegram_source_channel"] = False
            try:
                await update.message.delete()
            except Exception:
                pass

            msg_text = (
                "✅ *Fonte Telegram aggiunta*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Canale: `{clean}`\n\n"
                "Ho attivato automaticamente anche la categoria *Fonti Telegram 📥*.\n"
                "Ora puoi premere *Scansiona ora* per importare le offerte nel buffer."
            )
            keyboard = build_telegram_sources_menu(user_id)
            prompt_id = context.user_data.get("telegram_source_prompt_msg_id")
            if prompt_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=prompt_id,
                        text=msg_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard
                    )
                except Exception:
                    await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        except Exception as exc:
            logger.exception("[TG-SOURCES] Errore salvataggio canale sorgente")
            await update.message.reply_text(f"⚠️ Errore salvataggio fonte: `{exc}`", parse_mode=ParseMode.MARKDOWN)
        return

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
        data = load_user_data()
        exp = data.get(str(user_id), {}).get("license_expires", "?")
        notified = await notify_user_license_activated(context.bot, user_id, giorni, exp)
        await update.message.reply_text(
            (
                f"✅ Licenza attivata per `{user_id}` per `{giorni}` giorni.\n"
                f"📅 Scadenza: *{exp}*\n"
                f"📩 Notifica utente: *{'inviata' if notified else 'non inviata'}*"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

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


async def run_telegram_sources_scan_background(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    message_id: int,
    sources: list[str],
    limit: int,
) -> None:
    """Esegue la scansione Fonti Telegram fuori dal callback handler.

    In questo modo Telegram resta reattivo: l'admin può continuare a premere
    pulsanti e usare la dashboard mentre l'import lavora in background.
    """
    from src.telegram_sources.importer import import_channel_offers_to_buffer

    try:
        results = []
        for ch in sources:
            logger.info("[TG-SOURCES][MANUAL-BG] Scansione background %s user=%s limit=%s", ch, user_id, limit)
            res = await asyncio.to_thread(import_channel_offers_to_buffer, user_id, ch, limit)
            results.append(res)

        total_links = sum(r.found_links for r in results)
        total_asins = sum(r.found_asins for r in results)
        total_added = sum(r.added_products for r in results)
        total_discarded = sum(getattr(r, "discarded_products", 0) for r in results)
        total_errors = sum(r.errors for r in results)

        detail_lines = []
        for r in results:
            extra = "" if not getattr(r, "message", "") or r.message == "ok" else f" — {r.message}"
            detail_lines.append(
                f"• `{r.channel}` → 🔗 {r.found_links} | ASIN {r.found_asins} | ✅ {r.added_products} | 🗑️ {getattr(r, 'discarded_products', 0)}{extra}"
            )

        result_text = (
            "✅ *Scansione Fonti Telegram completata*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🔗 Link Amazon trovati: *{total_links}*\n"
            f"🧩 ASIN recuperati: *{total_asins}*\n"
            f"📦 Prodotti aggiunti al buffer: *{total_added}*\n"
            f"🗑️ Scartati: *{total_discarded}*\n"
            f"⚠️ Errori: *{total_errors}*\n\n"
            "📋 *Dettaglio*\n"
            + ("\n".join(detail_lines) if detail_lines else "Nessun dettaglio disponibile")
            + "\n\nIl buffer usato è: *Fonti Telegram 📥*."
        )

    except Exception as exc:
        logger.exception("[TG-SOURCES][MANUAL-BG] Errore scansione manuale background user=%s", user_id)
        result_text = (
            "❌ *Errore scansione Fonti Telegram*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Dettaglio: `{escape_markdown(str(exc), version=2)}`\n\n"
            "Puoi riprovare tra qualche minuto."
        )

    finally:
        _ACTIVE_TELEGRAM_SOURCE_SCANS.discard(user_id)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=result_text,
            reply_markup=build_telegram_sources_menu(user_id),
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest as exc:
        logger.warning("[TG-SOURCES][MANUAL-BG] Impossibile aggiornare messaggio dashboard: %s", exc)
    except Exception:
        logger.exception("[TG-SOURCES][MANUAL-BG] Errore aggiornamento finale dashboard")


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
        keyboard = [[InlineKeyboardButton("🔙 Dashboard", callback_data="back_to_menu")]]
        await query.edit_message_text(
            "ℹ️ *Informazioni*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Questo bot aiuta a monitorare offerte Amazon e pubblicarle su Telegram.\n\n"
            "✅ Selezioni le categorie\n"
            "✅ Imposti filtri e tempi\n"
            "✅ Colleghi i tuoi canali\n"
            "✅ Il bot prepara post puliti con link affiliato\n\n"
            "Suggerimento: mantieni poche categorie attive e punta su offerte credibili.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # SETTINGS
    # -------------------------------------------------------------------------
    if data == "settings":
        await query.edit_message_text(
            build_settings_text(user_id),
            reply_markup=build_settings_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FUNZIONI
    # -------------------------------------------------------------------------
    if data == "funzioni_menu":
        await query.edit_message_text(
            build_functions_text(),
            reply_markup=build_functions_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # FONTI TELEGRAM
    # -------------------------------------------------------------------------
    if data == "telegram_sources_menu":
        await query.edit_message_text(
            build_telegram_sources_text(user_id),
            reply_markup=build_telegram_sources_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "telegram_source_guide":
        await query.edit_message_text(
            build_telegram_sources_guide_text(),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Fonti Telegram", callback_data="telegram_sources_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "telegram_source_limit_menu":
        await query.edit_message_text(
            build_telegram_source_limit_text(user_id),
            reply_markup=build_telegram_source_limit_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "telegram_source_limit_guide":
        await query.edit_message_text(
            build_telegram_source_limit_guide_text(),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Limite lettura", callback_data="telegram_source_limit_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("telegram_source_limit_"):
        try:
            new_limit = int(data.rsplit("_", 1)[1])
        except Exception:
            new_limit = 30
        set_user_telegram_source_limit(user_id, new_limit)
        await query.edit_message_text(
            "✅ *Limite lettura aggiornato*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Da ora il bot leggerà *{new_limit} messaggi* per ogni fonte Telegram.\n\n"
            "La prossima scansione userà subito questo valore.",
            reply_markup=build_telegram_source_limit_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "telegram_source_add":
        context.user_data["awaiting_telegram_source_channel"] = True
        context.user_data["telegram_source_prompt_msg_id"] = query.message.message_id
        await query.edit_message_text(
            "➕ *Aggiungi fonte Telegram*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Inviami l’`@username` del canale pubblico da cui vuoi prelevare le offerte.\n\n"
            "Esempi:\n"
            "`@nomecanale`\n"
            "`https://t.me/nomecanale`\n\n"
            "Il bot leggerà la preview pubblica del canale e importerà solo link Amazon validi.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annulla", callback_data="telegram_sources_menu")]])
        )
        return

    if data.startswith("telegram_source_remove_"):
        ch = "@" + data.split("telegram_source_remove_", 1)[1]
        remove_user_telegram_source_channel(user_id, ch)
        await query.edit_message_text(
            f"🗑️ Fonte rimossa: `{ch}`\n\n" + build_telegram_sources_text(user_id),
            reply_markup=build_telegram_sources_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "telegram_source_scan_all":
        sources = get_user_telegram_source_channels(user_id)
        if not sources:
            await query.edit_message_text(
                "📥 *Fonti Telegram*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Non hai ancora configurato nessun canale sorgente.",
                reply_markup=build_telegram_sources_menu(user_id),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if user_id in _ACTIVE_TELEGRAM_SOURCE_SCANS:
            await query.edit_message_text(
                "⏳ *Scansione già in corso*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Sto già leggendo le fonti Telegram configurate.\n\n"
                "Puoi continuare a usare la dashboard: appena finisce aggiorno questo messaggio con il riepilogo.",
                reply_markup=build_telegram_sources_menu(user_id),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        limit = get_user_telegram_source_limit(user_id)
        _ACTIVE_TELEGRAM_SOURCE_SCANS.add(user_id)

        await query.edit_message_text(
            "⏳ *Scansione fonti Telegram avviata*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📡 Fonti configurate: *{len(sources)}*\n"
            f"📖 Messaggi letti per fonte: *{limit}*\n\n"
            "La scansione ora gira in background.\n"
            "Puoi continuare a usare il bot: categorie, impostazioni e dashboard restano disponibili.\n\n"
            "Quando finisce aggiorno automaticamente questo messaggio con i risultati.",
            reply_markup=build_telegram_sources_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )

        context.application.create_task(
            run_telegram_sources_scan_background(
                context=context,
                user_id=user_id,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                sources=sources,
                limit=limit,
            )
        )
        return

    # -------------------------------------------------------------------------
    # BUFFER DASHBOARD
    # -------------------------------------------------------------------------
    if data == "buffer_dashboard":
        keyboard = [[InlineKeyboardButton("🔙 Dashboard", callback_data="back_to_menu")]]
        await query.edit_message_text(
            build_buffer_dashboard_text(user_id),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # CONNECT MENU
    # -------------------------------------------------------------------------
    if data == "connect_menu":
        await query.edit_message_text(
            "🔗 *Canali e social*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Collega le destinazioni dove pubblicare le offerte.\n\n"
            "📣 Telegram: consigliato per la pubblicazione principale\n"
            "📘 Facebook: opzionale\n"
            "📷 Instagram: opzionale",
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
            "⏱️ *Tempi di pubblicazione*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📆 Intervallo attuale: *{get_user_post_interval(user_id)} min*\n"
            f"🔢 Offerte per categoria: *{get_user_offers_per_cycle(user_id)}*\n"
            f"🕒 Ripubblicazione stesso ASIN: *dopo {get_user_days_limit(user_id)} giorni*\n"
            f"🧹 Pulizia buffer: *{get_user_buffer_clear_days(user_id) or 'manuale'}*\n\n"
            "Configura frequenza, limiti e giorni attivi.",
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
    # SCROLL CATEGORIE
    # -------------------------------------------------------------------------
    if data == "category_scrolls_menu":
        await query.edit_message_text(
            build_category_scrolls_text(user_id),
            reply_markup=build_category_scrolls_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "category_scrolls_guide":
        keyboard = [
            [InlineKeyboardButton("🔃 Imposta scroll", callback_data="category_scrolls_menu")],
            [InlineKeyboardButton("🔙 Impostazioni", callback_data="settings")],
        ]
        await query.edit_message_text(
            build_category_scrolls_guide_text(user_id),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("category_scrolls_") and data != "category_scrolls_guide":
        value = int(data.rsplit("_", 1)[1])
        set_user_category_scrolls(user_id, value)
        await query.answer(f"🔃 Scroll categorie impostati a {value}")

        users = load_user_data()
        categories = users.get(str(user_id), {}).get("categories", [])
        for cat_code in categories:
            try:
                delete_buffer_file(user_id, cat_code)
                logger.info(f"[SCROLL] Buffer cancellato dopo cambio scroll: {user_id}_{cat_code}")
            except Exception:
                logger.exception(f"[SCROLL] Errore cancellazione buffer {user_id}_{cat_code}")

        await query.edit_message_text(
            f"✅ *Scroll categorie aggiornati*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Nuovo valore: *{value} scroll*\n\n"
            "Ho cancellato i buffer delle categorie attive, così il prossimo refill userà subito la nuova profondità di scansione.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔃 Torna agli scroll", callback_data="category_scrolls_menu")],
                [InlineKeyboardButton("🔙 Impostazioni", callback_data="settings")],
            ]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # PAGINE CATEGORIE
    # -------------------------------------------------------------------------
    if data == "category_pages_menu":
        await query.edit_message_text(
            build_category_pages_text(user_id),
            reply_markup=build_category_pages_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "category_pages_guide":
        keyboard = [
            [InlineKeyboardButton("📄 Imposta pagine", callback_data="category_pages_menu")],
            [InlineKeyboardButton("🔙 Impostazioni", callback_data="settings")],
        ]
        await query.edit_message_text(
            build_category_pages_guide_text(user_id),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("category_pages_") and data != "category_pages_guide":
        value = int(data.rsplit("_", 1)[1])
        set_user_category_pages(user_id, value)
        await query.answer(f"📄 Pagine categorie impostate a {value}")

        users = load_user_data()
        categories = users.get(str(user_id), {}).get("categories", [])
        for cat_code in categories:
            try:
                delete_buffer_file(user_id, cat_code)
                logger.info(f"[PAGES] Buffer cancellato dopo cambio pagine: {user_id}_{cat_code}")
            except Exception:
                logger.exception(f"[PAGES] Errore cancellazione buffer {user_id}_{cat_code}")

        await query.edit_message_text(
            f"✅ *Pagine categorie aggiornate*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Nuovo valore: *{value} pagine*\n\n"
            "Ho cancellato i buffer delle categorie attive, così il prossimo refill userà subito la nuova profondità di scansione.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Torna alle pagine", callback_data="category_pages_menu")],
                [InlineKeyboardButton("🔙 Impostazioni", callback_data="settings")],
            ]),
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
                text=build_category_text(user_id),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"[CATEGORIES] edit_message_text fallita: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=build_category_text(user_id),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # -------------------------------------------------------------------------
    # MENU PRINCIPALE
    # -------------------------------------------------------------------------
    if data == "back_to_menu":
        await query.edit_message_text(
            build_welcome_text(user.first_name, user_id),
            reply_markup=build_main_menu(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # -------------------------------------------------------------------------
    # ADMIN: GESTIONE LICENZE
    # -------------------------------------------------------------------------
    if data == "admin_license_menu":
        if not is_admin(user_id):
            await query.answer("🚫 Solo admin", show_alert=True)
            return

        context.user_data.pop("awaiting_admin_license_user_id", None)
        context.user_data.pop("awaiting_admin_license_custom_days", None)
        context.user_data.pop("admin_license_days", None)
        context.user_data["admin_license_prompt_msg_id"] = query.message.message_id

        await query.edit_message_text(
            build_admin_license_text(),
            reply_markup=build_admin_license_menu(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("admin_license_days_"):
        if not is_admin(user_id):
            await query.answer("🚫 Solo admin", show_alert=True)
            return

        days = int(data.rsplit("_", 1)[1])
        context.user_data["awaiting_admin_license_user_id"] = True
        context.user_data["admin_license_days"] = days
        context.user_data["admin_license_prompt_msg_id"] = query.message.message_id

        await query.edit_message_text(
            (
                "👑 *Gestione Licenze Admin*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"✅ Durata scelta: *{days} giorni*\n\n"
                "Ora inviami il *Telegram ID* dell’utente da attivare.\n\n"
                "Esempio: `1271567510`"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annulla", callback_data="admin_license_menu")]])
        )
        return

    if data == "admin_license_custom_days":
        if not is_admin(user_id):
            await query.answer("🚫 Solo admin", show_alert=True)
            return

        context.user_data["awaiting_admin_license_custom_days"] = True
        context.user_data["admin_license_prompt_msg_id"] = query.message.message_id

        await query.edit_message_text(
            (
                "✏️ *Durata manuale licenza*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Inviami il numero di giorni da assegnare.\n\n"
                "Esempio: `30`"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annulla", callback_data="admin_license_menu")]])
        )
        return

    if data == "admin_license_users":
        if not is_admin(user_id):
            await query.answer("🚫 Solo admin", show_alert=True)
            return

        users = load_user_data()
        rows = []
        for uid, info in users.items():
            status = "✅" if license_is_valid(info) else "⚠️" if info.get("has_license") else "❌"
            exp = info.get("license_expires", "-")
            rows.append(f"{status} `{uid}` · {exp}")

        if not rows:
            body = "Nessun utente registrato."
        else:
            body = "\n".join(rows[:30])
            if len(rows) > 30:
                body += f"\n… altri {len(rows) - 30} utenti"

        await query.edit_message_text(
            "📋 *Utenti registrati*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{body}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Gestione Licenze", callback_data="admin_license_menu")]])
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
        ]

        if is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Gestisci Licenze", callback_data="admin_license_menu")])

        keyboard.append([InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")])

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
    # ADMIN: ATTIVAZIONE LICENZA DA PANNELLO
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_admin_license_custom_days"):
        if not is_admin(user_id):
            context.user_data.pop("awaiting_admin_license_custom_days", None)
            await update.message.reply_text("🚫 Operazione riservata all’amministratore.")
            return

        try:
            days = int(text.strip())
            if days <= 0 or days > 3650:
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Durata non valida. Invia un numero di giorni, esempio: `30`", parse_mode=ParseMode.MARKDOWN)
            return

        context.user_data["awaiting_admin_license_custom_days"] = False
        context.user_data["awaiting_admin_license_user_id"] = True
        context.user_data["admin_license_days"] = days

        try:
            await update.message.delete()
        except Exception:
            pass

        prompt_id = context.user_data.get("admin_license_prompt_msg_id")
        text_msg = (
            "👑 *Gestione Licenze Admin*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"✅ Durata scelta: *{days} giorni*\n\n"
            "Ora inviami il *Telegram ID* dell’utente da attivare.\n\n"
            "Esempio: `1271567510`"
        )
        if prompt_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=prompt_id,
                    text=text_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annulla", callback_data="admin_license_menu")]])
                )
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=text_msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id=chat_id, text=text_msg, parse_mode=ParseMode.MARKDOWN)
        return

    if context.user_data.get("awaiting_admin_license_user_id"):
        from src.utils.license_manager import attiva_licenza

        if not is_admin(user_id):
            context.user_data.pop("awaiting_admin_license_user_id", None)
            await update.message.reply_text("🚫 Operazione riservata all’amministratore.")
            return

        days = int(context.user_data.get("admin_license_days", 30))
        try:
            target_user_id = int(text.strip())
        except Exception:
            await update.message.reply_text("❌ Telegram ID non valido. Invia solo numeri, esempio: `1271567510`", parse_mode=ParseMode.MARKDOWN)
            return

        try:
            attiva_licenza(target_user_id, days)
            data = load_user_data()
            exp = data.get(str(target_user_id), {}).get("license_expires", "?")
            notified = await notify_user_license_activated(context.bot, target_user_id, days, exp)

            context.user_data.pop("awaiting_admin_license_user_id", None)
            context.user_data.pop("admin_license_days", None)

            try:
                await update.message.delete()
            except Exception:
                pass

            result_text = (
                "✅ *Licenza attivata*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"👤 Utente: `{target_user_id}`\n"
                f"⏳ Durata: *{days} giorni*\n"
                f"📅 Scadenza: *{exp}*\n"
                f"📩 Notifica utente: *{'inviata' if notified else 'non inviata'}*\n\n"
                "Puoi attivare un’altra licenza oppure tornare al pannello."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Attiva altra licenza", callback_data="admin_license_menu")],
                [InlineKeyboardButton("🔙 Licenza & Affiliazione", callback_data="license_menu")],
            ])

            prompt_id = context.user_data.get("admin_license_prompt_msg_id")
            if prompt_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=prompt_id,
                        text=result_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard
                    )
                except Exception:
                    await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

        except Exception as e:
            logger.exception("[ADMIN_LICENSE] Errore attivazione da pannello")
            await update.message.reply_text(f"⚠️ Errore attivazione licenza: `{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    # -------------------------------------------------------------------------
    # FONTI TELEGRAM: AGGIUNGI CANALE SORGENTE
    # -------------------------------------------------------------------------
    if context.user_data.get("awaiting_telegram_source_channel"):
        from src.telegram_sources.importer import normalize_channel_name

        clean = normalize_channel_name(text)
        if not clean:
            await update.message.reply_text(
                "❌ Canale non valido. Inviami un canale pubblico, esempio: `@nomecanale` oppure `https://t.me/nomecanale`.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            add_user_telegram_source_channel(user_id, clean)
            context.user_data["awaiting_telegram_source_channel"] = False
            try:
                await update.message.delete()
            except Exception:
                pass

            msg_text = (
                "✅ *Fonte Telegram aggiunta*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Canale: `{clean}`\n\n"
                "Ho attivato automaticamente anche la categoria *Fonti Telegram 📥*.\n"
                "Ora puoi premere *Scansiona ora* per importare le offerte nel buffer."
            )
            keyboard = build_telegram_sources_menu(user_id)
            prompt_id = context.user_data.get("telegram_source_prompt_msg_id")
            if prompt_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=prompt_id,
                        text=msg_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard
                    )
                except Exception:
                    await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        except Exception as exc:
            logger.exception("[TG-SOURCES] Errore salvataggio canale sorgente")
            await update.message.reply_text(f"⚠️ Errore salvataggio fonte: `{exc}`", parse_mode=ParseMode.MARKDOWN)
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