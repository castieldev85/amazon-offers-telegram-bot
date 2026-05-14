
from src.scraper.product_scraper import extract_product_info
from src.utils.affiliate import get_affiliate_link
from telegram import InputMediaPhoto
from src.database.user_data_manager import load_user_data
import requests

async def publish_offer_to_channels(user_id: int, asin: str, context):
    product = extract_product_info(asin)
    if not product:
        await context.bot.send_message(chat_id=user_id, text="❌ Impossibile recuperare i dati del prodotto.")
        return

    link = get_affiliate_link(user_id, asin)
    caption_html = (
        f"🔥 <b>{product.title}</b>\n"
        f"💰 <b>{product.price}€</b>\n"
        f"📉 Sconto: <b>{product.discount}%</b>\n\n"
        f"{link}"
    )
    caption_text = (
        f"🔥 {product.title}\n"
        f"💰 Prezzo: {product.price}€\n"
        f"📉 Sconto: {product.discount}%\n\n"
        f"{link}"
    )

    data = load_user_data()
    user_data = data.get(str(user_id), {})

    # ➤ Telegram
    telegram_channels = user_data.get("telegram_channels", [])
    for ch in telegram_channels:
        try:
            await context.bot.send_photo(
                chat_id=ch,
                photo=product.image,
                caption=caption_html,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[PUBLISH] ❌ Telegram errore su {ch}:", e)

    # ➤ Facebook
    fb = user_data.get("facebook_config", {})
    if fb.get("page_id") and fb.get("access_token"):
        try:
            url = f"https://graph.facebook.com/{fb['page_id']}/photos"
            payload = {
                "url": product.image,
                "caption": caption_text,
                "access_token": fb["access_token"]
            }
            resp = requests.post(url, data=payload)
            if not resp.ok:
                print("[PUBLISH] ⚠️ Facebook errore:", resp.text)
        except Exception as e:
            print("[PUBLISH] ❌ Facebook exception:", e)

    # ➤ Instagram (se configurato)
    insta = fb
    if insta.get("instagram_business_id"):
        try:
            # 1. crea media object
            media_url = f"https://graph.facebook.com/v18.0/{insta['instagram_business_id']}/media"
            media_payload = {
                "image_url": product.image,
                "caption": caption_text,
                "access_token": insta["access_token"]
            }
            media_resp = requests.post(media_url, data=media_payload)
            media_result = media_resp.json()
            creation_id = media_result.get("id")

            # 2. pubblica media object
            if creation_id:
                publish_url = f"https://graph.facebook.com/v18.0/{insta['instagram_business_id']}/media_publish"
                publish_payload = {
                    "creation_id": creation_id,
                    "access_token": insta["access_token"]
                }
                pub_resp = requests.post(publish_url, data=publish_payload)
                if not pub_resp.ok:
                    print("[PUBLISH] ⚠️ Instagram publish errore:", pub_resp.text)
        except Exception as e:
            print("[PUBLISH] ❌ Instagram exception:", e)

    await context.bot.send_message(user_id, "✅ Offerta pubblicata dove configurato.")
