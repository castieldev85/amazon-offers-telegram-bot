import logging
import os
from datetime import datetime

import requests

from src.buffer.buffer_manager import load_buffered_products, remove_posted_asins
from src.configs.settings import BUFFER_PATH, TELEGRAM_BOT_TOKEN, ENABLE_FACEBOOK_POSTING, ENABLE_INSTAGRAM_POSTING
from src.database.user_data_manager import load_user_data
from src.utils.database_builder import is_valid_for_resend
from src.utils.facebook import publish_offer_to_facebook
from src.utils.image_builder import generate_multi_offer_image
from src.utils.image_prep import prepare_for_instagram
from src.utils.instagram_integration import API_VERSION
from src.utils.product import Product, shorten_title
from src.utils.shortlink_generator import generate_affiliate_link
from src.utils.your_cdn_uploader import upload_to_cdn

logger = logging.getLogger(__name__)


def _send_photo_sync(chat_id, photo_path: str, caption: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[MultiPost] TELEGRAM_BOT_TOKEN mancante")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo:
            res = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": photo},
                timeout=30,
            )
        if res.status_code != 200:
            logger.warning(f"[MultiPost] Telegram fallito chat={chat_id} status={res.status_code} body={res.text}")
            return False
        return True
    except Exception:
        logger.exception(f"[MultiPost] Errore Telegram chat={chat_id}")
        return False


def post_multi_offer_for_user(user_id: int, category: str, min_items: int = 2, max_items: int = 6):
    buffer_file = os.path.join(BUFFER_PATH, f"{user_id}_{category}.json")
    if not os.path.exists(buffer_file):
        logger.info(f"[MultiPost] Nessun buffer trovato per {category}")
        return False

    products = load_buffered_products(user_id, category)
    valid_products = [
        p for p in products
        if isinstance(p, Product)
        and getattr(p, "asin", None)
        and getattr(p, "title", None)
        and getattr(p, "price", None)
        and is_valid_for_resend(user_id, p.asin)
    ]

    if len(valid_products) < min_items:
        logger.info(f"[MultiPost] Offerte valide insufficienti in {category}: {len(valid_products)}")
        return False

    valid_products.sort(key=lambda p: float(getattr(p, "discount", 0) or 0), reverse=True)
    products_to_post = valid_products[:max_items]

    image_path = generate_multi_offer_image(products_to_post)
    caption = f"🔥 Offerte top per *{category.replace('cat_', '').replace('_', ' ').title()}*:\n\n"
    for p in products_to_post:
        link = generate_affiliate_link(user_id, p.asin)
        caption += f"🔹 *{shorten_title(p.title, 70)}*\n"
        caption += f"Prezzo: *{p.price}€*"
        if p.old_price:
            caption += f"  ~{p.old_price}€~"
        caption += "\n"
        if p.discount:
            caption += f"Sconto: *-{p.discount}%*\n"
        if getattr(p, "has_coupon", False):
            caption += "🎟 Coupon disponibile\n"
        caption += f"[Acquista ora]({link})\n\n"
    caption += f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    data = load_user_data().get(str(user_id), {})
    targets = data.get("telegram_channels") or [user_id]

    telegram_ok = False
    for ch in targets:
        telegram_ok = _send_photo_sync(ch, image_path, caption) or telegram_ok

    if ENABLE_FACEBOOK_POSTING:
        try:
            publish_offer_to_facebook(user_id, products_to_post[0], image_path)
        except Exception:
            logger.exception("[MultiPost] Errore Facebook")

    if ENABLE_INSTAGRAM_POSTING:
        insta_cfg = data.get("instagram_config", {})
        if insta_cfg.get("access_token") and insta_cfg.get("instagram_business_id"):
            try:
                ig_img = prepare_for_instagram(image_path)
                image_url = upload_to_cdn(ig_img)
                caption_ig = "\n".join([shorten_title(p.title, 80) for p in products_to_post]) + "\nScopri ora su Amazon!"
                container = requests.post(
                    f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media",
                    params={"image_url": image_url, "caption": caption_ig, "access_token": insta_cfg["access_token"]},
                    timeout=30,
                ).json()
                cid = container.get("id")
                if cid:
                    requests.post(
                        f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media_publish",
                        params={"creation_id": cid, "access_token": insta_cfg["access_token"]},
                        timeout=30,
                    )
            except Exception:
                logger.exception("[MultiPost] Errore Instagram")

    if telegram_ok:
        remove_posted_asins(user_id, category, products_to_post)
        logger.info(f"[MultiPost] Completato per {category}, rimossi {len(products_to_post)} prodotti")

    try:
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
    except Exception:
        pass

    return telegram_ok
