import re
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.utils.amazon_parser import extract_product_info
from src.utils.product import Product
from src.utils.affiliate import get_affiliate_link as generate_affiliate_link
from src.utils.image_builder import crea_immagine_offerta_da_url


# ---------------------------------------------------------
# 🔍 Estrazione ASIN da qualsiasi link Amazon
# ---------------------------------------------------------
def extract_asin_from_url(url: str) -> str | None:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})"
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------
# 📥 Gestione link manuale
# ---------------------------------------------------------
async def handle_manual_link(update, context):
    user_id = update.effective_user.id
    url = update.message.text.strip()

    # Espansione shortlink amzn.to
    if "amzn.to" in url:
        try:
            r = requests.head(url, allow_redirects=True, timeout=10)
            url = r.url
        except Exception as e:
            await update.message.reply_text(f"❌ Errore espansione shortlink: {e}")
            return

    # Controllo dominio
    if "amazon.it" not in url:
        await update.message.reply_text("❌ Inserisci un link Amazon valido (.it)")
        return

    # Estrazione ASIN
    asin = extract_asin_from_url(url)
    if not asin:
        await update.message.reply_text("❌ Non riesco a trovare l'ASIN nel link.")
        return

    # Scraping/API
    product = extract_product_info(asin)
    if not product or not product.asin:
        await update.message.reply_text("❌ Impossibile estrarre i dati del prodotto.")
        return

    # Salvataggio per conferma
    context.user_data["pending_manual_post"] = product
    affiliate_link = generate_affiliate_link(user_id, product.asin)

    # --- Messaggio ---
    caption = f"📌 <b>{product.title}</b>\n"
    caption += f"💰 Prezzo: {product.price}€\n"

    if product.old_price and product.old_price > product.price:
        caption += f"💸 Anziché: <s>{product.old_price}€</s>\n"

    if product.discount:
        caption += f"📉 Sconto: <b>{product.discount}%</b>\n"

    caption += f"\n👉 {affiliate_link}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sì, pubblica", callback_data=f"confirm_manual_post:{product.asin}")],
        [InlineKeyboardButton("❌ Annulla", callback_data="cancel_manual_post")]
    ])

    # Invio immagine o testo
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=product.image,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except:
        await update.message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )


# ---------------------------------------------------------
# 📤 Conferma pubblicazione
# ---------------------------------------------------------
async def confirm_manual_post(update, context):
    query = update.callback_query
    await query.answer()

    product = context.user_data.get("pending_manual_post")
    if not product:
        await query.edit_message_text("❌ Nessun prodotto in memoria.")
        return

    # Crea immagine personalizzata
    img_path = crea_immagine_offerta_da_url(
        url=product.image,
        prezzo=product.price,
        sconto=product.discount,
        vecchio_prezzo=product.old_price,
        asin=product.asin
    )

    from src.utils.product import build_offer_message
    message, markup = build_offer_message(product, update.effective_user.id)

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=open(img_path, "rb"),
        caption=message,
        parse_mode="MarkdownV2",
        reply_markup=markup
    )

    await query.delete_message()


# ---------------------------------------------------------
# ❌ Annullamento
# ---------------------------------------------------------
async def cancel_manual_post(update, context):
    await update.callback_query.answer("❌ Annullato")
    await update.callback_query.message.delete()
