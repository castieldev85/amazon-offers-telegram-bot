import json
import logging
import os
import time
from typing import Any, Dict

import requests
from PIL import Image, ImageStat

from src.database.user_data_manager import load_user_data, save_user_data
from src.utils.shortlink_generator import generate_affiliate_link
from src.utils.offer_scorer import estimate_final_price, parse_price

logger = logging.getLogger(__name__)

API_VERSION = "v23.0"

# Quanto fermarsi se Facebook risponde con blocco anti-spam
FACEBOOK_BLOCK_HOURS = 24

# Retry solo per errori temporanei di rete / 5xx
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 5


def _get_facebook_config(user_id: int) -> Dict[str, Any]:
    data = load_user_data()
    return data.get(str(user_id), {}).get("facebook_config", {}) or {}


def _save_facebook_block(user_id: int, seconds: int, error_payload: Dict[str, Any] | None = None):
    data = load_user_data()
    uid = str(user_id)
    data.setdefault(uid, {})
    data[uid].setdefault("facebook_config", {})

    blocked_until = time.time() + seconds
    data[uid]["facebook_config"]["blocked_until"] = blocked_until
    data[uid]["facebook_config"]["last_block_at"] = int(time.time())

    if error_payload:
        data[uid]["facebook_config"]["last_error"] = error_payload

    save_user_data(data)
    logger.warning(
        f"[FACEBOOK] ⛔ Blocco salvato per user {user_id} fino a {int(blocked_until)}"
    )


def _is_facebook_blocked(user_id: int) -> bool:
    cfg = _get_facebook_config(user_id)
    blocked_until = float(cfg.get("blocked_until", 0) or 0)
    now = time.time()

    if now < blocked_until:
        remaining = int(blocked_until - now)
        logger.warning(
            f"[FACEBOOK] ⏸️ Pubblicazione sospesa per user {user_id}. Mancano {remaining}s."
        )
        return True

    return False


def _parse_fb_error(response_text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(response_text)
        return payload.get("error", {}) if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _format_euro(value) -> str:
    parsed = parse_price(value)
    if parsed is None:
        raw = str(value).strip() if value is not None else "N/D"
        return raw if raw else "N/D"
    return f"{parsed:.2f}".replace(".", ",")


def _format_discount(value) -> str:
    try:
        if value is None:
            return "0"
        val = float(str(value).replace("%", "").replace(",", ".").strip())
        if val <= 0:
            return "0"
        if float(val).is_integer():
            return str(int(val))
        return f"{val:.1f}".replace(".", ",")
    except Exception:
        return "0"




def _prepare_facebook_image(image_path: str) -> str | None:
    """Prepara una JPEG valida per Facebook e ritorna il path da caricare.

    Su Windows/Graph API alcuni upload possono risultare anomali se il file è
    progressivo, ha alpha channel o metadata strani. Convertiamo sempre in RGB
    JPEG standard e controlliamo che non sia un file vuoto/illeggibile.
    """
    if not image_path:
        return None

    image_path = os.path.abspath(str(image_path))
    if not os.path.exists(image_path):
        logger.warning(f"[FACEBOOK] ⚠️ File immagine non trovato: {image_path}")
        return None

    try:
        if os.path.getsize(image_path) < 1024:
            logger.warning(f"[FACEBOOK] ⚠️ File immagine troppo piccolo: {image_path}")
            return None

        with Image.open(image_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w < 200 or h < 200:
                logger.warning(f"[FACEBOOK] ⚠️ Immagine troppo piccola per Facebook: {w}x{h}")
                return None

            # Se l'immagine è quasi completamente bianca/monocolore, la segnaliamo
            # ma la pubblichiamo comunque: può essere un placeholder intenzionale.
            try:
                stat = ImageStat.Stat(im.resize((32, 32)))
                variance = sum(stat.var) / max(len(stat.var), 1)
                if variance < 3:
                    logger.warning(f"[FACEBOOK] ⚠️ Immagine quasi vuota/monocolore: variance={variance:.2f}")
            except Exception:
                pass

            out_dir = os.path.join(os.path.dirname(image_path), "facebook_ready")
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(image_path))[0]
            out_path = os.path.join(out_dir, f"{base}_fb.jpg")
            im.save(out_path, format="JPEG", quality=92, optimize=False, progressive=False)

        logger.info(f"[FACEBOOK] 🖼️ Immagine preparata per upload: {out_path} ({os.path.getsize(out_path)} bytes)")
        return out_path
    except Exception:
        logger.exception(f"[FACEBOOK] ❌ Impossibile preparare immagine Facebook: {image_path}")
        return None

def publish_to_facebook_file(user_id: int, image_path: str, caption: str) -> bool:
    logger.info(f"[FACEBOOK] ▶️ Inizio pubblicazione per user {user_id}")
    logger.info(f"[FACEBOOK] ✅ Immagine: {image_path}")
    logger.info(f"[FACEBOOK] ✅ Caption: {caption[:100]}...")

    if _is_facebook_blocked(user_id):
        return False

    user_data = _get_facebook_config(user_id)
    page_id = user_data.get("page_id")
    token = user_data.get("access_token")

    if not (page_id and token):
        logger.warning("[FACEBOOK] ⚠️ Configurazione mancante")
        return False

    upload_image_path = _prepare_facebook_image(image_path)
    if not upload_image_path:
        logger.warning(f"[FACEBOOK] ⚠️ Immagine non valida, salto Facebook: {image_path}")
        return False

    url = f"https://graph.facebook.com/{API_VERSION}/{page_id}/photos"

    for i in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(f"[FACEBOOK] 🚀 Invio richiesta a Facebook Graph API... tentativo {i}/{MAX_ATTEMPTS}")

            with open(upload_image_path, "rb") as img:
                filename = os.path.basename(upload_image_path)
                res = requests.post(
                    url,
                    data={
                        "message": caption,
                        "caption": caption,
                        "published": "true",
                        "no_story": "false",
                        "access_token": token,
                    },
                    files={"source": (filename, img, "image/jpeg")},
                    timeout=90,
                )

            logger.info(f"[FACEBOOK] Tentativo {i}/{MAX_ATTEMPTS} - Status Code: {res.status_code}")
            logger.info(f"[FACEBOOK] 📦 Risposta: {res.text}")

            if res.status_code == 200:
                logger.info(f"[FACEBOOK] ✅ Pubblicazione completata per user {user_id}")
                return True

            error_info = _parse_fb_error(res.text)
            error_code = error_info.get("code")
            error_subcode = error_info.get("error_subcode")
            fbtrace_id = error_info.get("fbtrace_id")
            error_type = error_info.get("type")
            error_message = error_info.get("message")

            logger.warning(
                f"[FACEBOOK] ⚠️ Errore API user={user_id} "
                f"code={error_code} subcode={error_subcode} type={error_type} fbtrace_id={fbtrace_id}"
            )

            # BLOCCO ANTI-SPAM / LIMITAZIONE META
            if error_code == 368:
                logger.warning(
                    f"[FACEBOOK] ⛔ Blocco anti-spam rilevato per user {user_id}. "
                    f"Interrompo subito i retry."
                )
                _save_facebook_block(
                    user_id=user_id,
                    seconds=FACEBOOK_BLOCK_HOURS * 3600,
                    error_payload={
                        "code": error_code,
                        "subcode": error_subcode,
                        "type": error_type,
                        "message": error_message,
                        "fbtrace_id": fbtrace_id,
                        "raw_response": res.text,
                        "at": int(time.time()),
                    }
                )
                return False

            # Token scaduto/non valido: inutile ritentare
            if error_code in (190,):
                logger.error(f"[FACEBOOK] 🔑 Token Facebook non valido/scaduto per user {user_id}")
                return False

            # Errori 4xx generici: inutile martellare
            if 400 <= res.status_code < 500:
                logger.error(f"[FACEBOOK] ❌ Errore client {res.status_code}, stop retry.")
                return False

            # Errori 5xx: ritenta
            if res.status_code >= 500 and i < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
                continue

            return False

        except requests.RequestException as e:
            logger.error(
                f"[FACEBOOK] ❌ Errore rete tentativo {i}/{MAX_ATTEMPTS} "
                f"- Tipo: {type(e).__name__} - Dettagli: {e}"
            )
            if i < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
            else:
                return False
        except Exception as e:
            logger.error(
                f"[FACEBOOK] ❌ Errore tentativo {i}/{MAX_ATTEMPTS} "
                f"- Tipo: {type(e).__name__} - Dettagli: {e}"
            )
            return False

    return False


def publish_offer_to_facebook(user_id: int, product, image_path: str) -> bool:
    link = generate_affiliate_link(user_id, product.asin)

    offer_info = estimate_final_price(product)
    current_price = offer_info.get("current_price")
    old_price = offer_info.get("old_price")
    estimated_final_price = offer_info.get("estimated_final_price")
    total_estimated_discount = offer_info.get("total_estimated_discount_percent")
    coupon_info = offer_info.get("coupon_info", {}) or {}
    promo_info = offer_info.get("promo_info", {}) or {}

    title = str(getattr(product, "title", "Prodotto Amazon")).strip()
    current_price_text = _format_euro(current_price if current_price is not None else getattr(product, "price", None))
    old_price_text = _format_euro(old_price if old_price is not None else getattr(product, "old_price", None))
    final_price_text = _format_euro(estimated_final_price) if estimated_final_price is not None else None

    discount_value = _format_discount(getattr(product, "discount", 0))
    total_discount_value = _format_discount(total_estimated_discount) if total_estimated_discount is not None else None

    coupon_text = str(getattr(product, "coupon_text", "") or "").strip()
    promo_code = str(getattr(product, "promo_code", "") or "").strip()
    is_limited_offer = bool(getattr(product, "is_limited_offer", False))

    caption = f"🔥 {title}\n"

    if old_price and old_price > 0 and current_price and old_price > current_price:
        caption += f"💰 Prezzo: {current_price_text}€ invece di {old_price_text}€\n"
    else:
        caption += f"💰 Prezzo: {current_price_text}€\n"

    if float(discount_value.replace(",", ".")) > 0:
        caption += f"🎯 Sconto base: -{discount_value}%\n"

    if getattr(product, "has_coupon", False):
        if coupon_text:
            caption += f"🏷️ Coupon: {coupon_text}\n"
        else:
            caption += "🏷️ Coupon disponibile!\n"

    if promo_code:
        caption += f"🔐 Codice sconto: {promo_code}\n"

    if estimated_final_price is not None and current_price is not None and estimated_final_price < current_price:
        caption += f"📉 Prezzo finale stimato: {final_price_text}€\n"

    if total_discount_value and float(total_discount_value.replace(",", ".")) > 0:
        caption += f"🚀 Sconto totale stimato: -{total_discount_value}%\n"

    coupon_percent = coupon_info.get("coupon_value") if coupon_info.get("coupon_type") == "percent" else None
    promo_percent = promo_info.get("promo_percent")

    if coupon_percent:
        caption += f"💡 Coupon stimato: -{_format_discount(coupon_percent)}%\n"

    if promo_percent:
        caption += f"💡 Promo stimata: -{_format_discount(promo_percent)}%\n"

    if is_limited_offer:
        caption += "⏰ Offerta a tempo!\n"

    caption += f"\n👉 {link}"

    return publish_to_facebook_file(user_id, image_path, caption)