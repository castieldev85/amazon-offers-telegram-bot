import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.database.user_data_manager import load_user_data, save_user_data

logger = logging.getLogger(__name__)
API_VERSION = "v19.0"


def get_client_config(chat_id: int) -> dict:
    data = load_user_data()
    return data.get(str(chat_id), {})


def save_client_config(chat_id: int, config: dict) -> None:
    data = load_user_data()
    data[str(chat_id)] = config
    save_user_data(data)


async def show_instagram_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Mostra il menu di configurazione Instagram con pulsante di ritorno.
    """
    query = update.callback_query
    chat_id = query.message.chat.id
    config = get_client_config(chat_id).get("instagram_config", {})

    # Bottone di ritorno alle impostazioni
    back_button = InlineKeyboardButton("🔙 Torna alle Impostazioni", callback_data="settings")

    if config.get("access_token"):
        text = (
            f"📷 <b>Account Instagram collegato</b>\n\n"
            f"Username: {config.get('username', '—')}\n"
            f"ID: {config.get('instagram_business_id', '—')}\n\n"
            "Scegli un’opzione:"
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❌ Scollega", callback_data="unlink_instagram"),
                InlineKeyboardButton("🔄 Cambia", callback_data="change_instagram")
            ],
            [ back_button ]
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        await query.answer()
    else:
        # Chiedi il token
        context.user_data["awaiting_instagram_token"] = True
        context.user_data.pop("awaiting_instagram_page_id", None)
        context.user_data.pop("pending_ig_token", None)
        text = (
            "📷 <b>Collega Account Instagram</b>\n\n"
            "❗️ Inviami ora il tuo <b>Access Token</b> Instagram:"
        )
        markup = InlineKeyboardMarkup([
            [ back_button ]
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        await query.answer()


async def handle_instagram_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestisce l'inserimento del token Instagram da parte dell'utente, sia User che Page Access Token.
    Dopo conferma, mostra automaticamente il menu "Collega Canali".
    """
    chat_id = update.effective_chat.id
        # Cancella il messaggio dell’utente (token o page ID)
    try:
        await update.message.delete()
    except Exception:
        pass


    # Caso Page Access Token: attendi Page ID
    if context.user_data.get("awaiting_instagram_page_id"):
        token = context.user_data.pop("pending_ig_token")
        page_id = update.message.text.strip()
        # Ottieni IG Business ID
        ig_res = requests.get(
            f"https://graph.facebook.com/{API_VERSION}/{page_id}",
            params={"fields": "instagram_business_account", "access_token": token}
        ).json()
        ig_id = ig_res.get("instagram_business_account", {}).get("id")
        if not ig_id:
            await update.message.reply_text(
                "❌ ID Pagina non valido o nessun account Instagram Business collegato."
            )
            return
        # Recupera username
        user_res = requests.get(
            f"https://graph.facebook.com/{API_VERSION}/{ig_id}",
            params={"fields": "username", "access_token": token}
        ).json()
        username = user_res.get("username", "—")
        # Salva
        cfg = get_client_config(chat_id)
        cfg["instagram_config"] = {
            "access_token": token,
            "page_id": page_id,
            "instagram_business_id": ig_id,
            "user_id": ig_id,
            "username": username
        }
        save_client_config(chat_id, cfg)
        # Conferma e menu
        await update.message.reply_text(
            f"✅ Account Instagram collegato!\n• <b>{username}</b> (ID {ig_id})",
            parse_mode="HTML"
        )
    else:
        # Caso User Access Token
        if not context.user_data.get("awaiting_instagram_token"):
            return
        token = update.message.text.strip()
        # Prova a ottenere Pagine
        pages_res = requests.get(
            f"https://graph.facebook.com/{API_VERSION}/me/accounts",
            params={"access_token": token}
        ).json()
        pages = pages_res.get("data", [])
        if not pages:
            # è un Page Token: chiedi Page ID
            context.user_data["pending_ig_token"] = token
            context.user_data["awaiting_instagram_page_id"] = True
            context.user_data.pop("awaiting_instagram_token", None)
            await update.message.reply_text(
                "❗️ Non ho trovato pagine con questo token.\n"
                "Se stai usando un Page Access Token, invia ora l’ID della Pagina Instagram."
            )
            return
        page_id = pages[0]["id"]
        # Ottieni IG Business ID
        ig_res = requests.get(
            f"https://graph.facebook.com/{API_VERSION}/{page_id}",
            params={"fields": "instagram_business_account", "access_token": token}
        ).json()
        ig_id = ig_res.get("instagram_business_account", {}).get("id")
        if not ig_id:
            await update.message.reply_text(
                "❌ Nessun account Instagram Business collegato alla Pagina."
            )
            return
        # Recupera username
        user_res = requests.get(
            f"https://graph.facebook.com/{API_VERSION}/{ig_id}",
            params={"fields": "username", "access_token": token}
        ).json()
        username = user_res.get("username", "—")
        # Salva
        cfg = get_client_config(chat_id)
        cfg["instagram_config"] = {
            "access_token": token,
            "page_id": page_id,
            "instagram_business_id": ig_id,
            "user_id": ig_id,
            "username": username
        }
        save_client_config(chat_id, cfg)
        # Conferma
        await update.message.reply_text(
            f"✅ Account Instagram collegato!\n• <b>{username}</b> (ID {ig_id})",
            parse_mode="HTML"
        )
    # Rimuovi stato
    context.user_data.pop("awaiting_instagram_token", None)
    context.user_data.pop("awaiting_instagram_page_id", None)

    # ** Ritorna automatico al menu "Collega Canali" **
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Canali / Gruppi Telegram", callback_data="connect_telegram")],
        [InlineKeyboardButton("📘 Account Facebook",         callback_data="connect_facebook")],
        [InlineKeyboardButton("📷 Account Instagram",        callback_data="connect_instagram")],
        [InlineKeyboardButton("🔙 Torna alle Impostazioni",  callback_data="settings")]
    ])
    await update.message.reply_text(
        "🔗 *Collega i tuoi canali per ricevere automaticamente le offerte:*\n\n"
        "Scegli una piattaforma da configurare:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def handle_instagram_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestisce le callback inline per scollegare o cambiare il token Instagram.
    """
    query = update.callback_query
    data = query.data
    chat_id = query.message.chat.id
    config = get_client_config(chat_id)

    if data == "unlink_instagram":
        config.pop("instagram_config", None)
        save_client_config(chat_id, config)
        await query.answer("Account scollegato ✅")
        return await show_instagram_menu(update, context)

    if data == "change_instagram":
        context.user_data["awaiting_instagram_token"] = True
        context.user_data.pop("awaiting_instagram_page_id", None)
        await query.answer()
        return await query.edit_message_text(
            "📷 Inviami il <b>nuovo Access Token</b> Instagram:",
            parse_mode="HTML"
        )

    await query.answer("❌ Opzione non riconosciuta.")