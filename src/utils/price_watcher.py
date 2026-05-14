import asyncio
import logging

from telegram import Bot
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest

from src.configs.settings import TELEGRAM_BOT_TOKEN
from src.database.user_data_manager import load_user_data, save_user_data
from src.scraper.product_scraper import extract_product_info
from src.utils.image_builder import crea_immagine_offerta_da_url
from src.utils.offer_scorer import parse_price
from src.utils.product import Product, build_offer_message

logger = logging.getLogger(__name__)


def _get_bot() -> Bot:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN mancante")
    return Bot(token=TELEGRAM_BOT_TOKEN)


async def notify_user(user_id: int, product_obj: Product):
    img_path = None
    try:
        img_path = crea_immagine_offerta_da_url(
            url=product_obj.image,
            prezzo=product_obj.price,
            sconto=product_obj.discount,
            vecchio_prezzo=product_obj.old_price,
            asin=product_obj.asin,
        )
        caption, markup = build_offer_message(product_obj, user_id, category_name=None)
        bot = _get_bot()
        with open(img_path, "rb") as photo:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=caption,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
    except Exception:
        logger.exception(f"[WATCHER] Errore notifica utente {user_id}")


async def monitor_watchlist():
    logger.info("[WATCHER] Avvio controllo watchlist...")
    all_data = load_user_data()
    updated = False

    for uid, udata in all_data.items():
        try:
            user_id = int(uid)
        except Exception:
            continue

        watchlist = udata.get("watchlist", [])
        already_notified = udata.setdefault("already_notified", {})

        for item in watchlist:
            asin = item.get("asin")
            threshold = item.get("threshold")
            try:
                product = extract_product_info(asin)
                current_price = parse_price(getattr(product, "price", None)) if product else None
                threshold_price = parse_price(threshold)
                if current_price is None or threshold_price is None:
                    continue

                notified_price = already_notified.get(asin)
                if current_price <= threshold_price and (not notified_price or current_price < float(notified_price)):
                    already_notified[asin] = current_price
                    updated = True
                    await notify_user(user_id, product)
            except Exception:
                logger.exception(f"[WATCHER] Errore ASIN={asin} user={user_id}")

    if updated:
        save_user_data(all_data)


async def start_price_watcher():
    while True:
        await monitor_watchlist()
        await asyncio.sleep(300)


async def handle_track_button(update, context):
    await update.callback_query.answer()
    asin = update.callback_query.data.split("_", 1)[1]

    if update.effective_chat.type != ChatType.PRIVATE:
        start_link = f"https://t.me/{context.bot.username}?start=track_{asin}"
        await update.effective_chat.send_message(
            f"👋 Per monitorare questo prodotto, apri la chat privata:\n👉 {start_link}",
            disable_web_page_preview=True,
        )
        return

    context.user_data["awaiting_threshold_for"] = asin
    try:
        await update.callback_query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        pass
    await update.effective_chat.send_message(
        f"📊 Inserisci il prezzo soglia per l’ASIN `{asin}` (es: 25.99):",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_start_command(update, context):
    message = update.message
    if message and message.text and message.text.startswith("/start track_"):
        asin = message.text.split("_", 1)[1]
        context.user_data["awaiting_threshold_for"] = asin
        await message.reply_text(
            f"📊 Inserisci il prezzo soglia per l’ASIN `{asin}` (es: 25.99):",
            parse_mode=ParseMode.MARKDOWN,
        )
