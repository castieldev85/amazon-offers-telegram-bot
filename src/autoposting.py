import os
import asyncio
import time
import logging
import requests
import urllib3
import json
from telegram.constants import ParseMode
from src.utils.image_prep import prepare_for_instagram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime
import random
from telegram.error import RetryAfter, TimedOut, NetworkError
from src.utils.offer_scorer import score_super_offer, estimate_final_price, build_offer_debug_summary, parse_price

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src.database.user_data_manager import (
    load_user_data,
    save_user_data,
    get_user_categories,
    get_user_post_interval,
    get_user_offers_per_cycle,
)
from src.buffer.buffer_manager import (
    load_buffered_products,
    remove_posted_asins,
    needs_refill,
    delete_buffer_file,
    save_buffered_products,
    delete_category_if_empty  # <-- import aggiunto
)
from src.utils.your_cdn_uploader import upload_to_cdn
from src.utils.instagram_integration import API_VERSION
from src.buffer.refill_base import refill_buffer_for_user
from src.buffer.rejected_offers import mark_rejected_asins, cleanup_expired_rejections
from src.utils.product import build_offer_message
from src.utils.shortlink_generator import generate_affiliate_link
from src.utils.image_builder import crea_immagine_offerta_da_url
from src.utils.database_builder import is_valid_for_resend
from src.utils.facebook import publish_offer_to_facebook
from src.configs.schedule_config import is_within_active_schedule, next_active_datetime, format_schedule_status
from src.configs.settings import (
    AUTOPOST_SLEEP_SECONDS,
    ENABLE_FACEBOOK_POSTING,
    ENABLE_INSTAGRAM_POSTING,
    MAX_OFFERS_PER_USER_CYCLE,
    MIN_OFFER_SCORE,
    REFILL_CHECK_INTERVAL_SECONDS,
    WATCHLIST_CHECK_INTERVAL_SECONDS,
)

# scraper imports
from src.scraper.abbigliamento_scraper import get_asins_from_abbigliamento
from src.scraper.electronics_scraper import get_asins_from_electronics
from src.scraper.deals_scraper import get_deals_asins
from src.scraper.casa_cucina_scraper import get_asins_from_casa_cucina
from src.scraper.bellezza_scraper import get_asins_from_bellezza
from src.scraper.sport_scraper import get_asins_from_sport
from src.scraper.giocattoli_scraper import get_asins_from_giocattoli
from src.scraper.faidate_scraper import get_asins_from_faidate
from src.scraper.auto_moto_scraper import get_asins_from_auto_moto
from src.scraper.libri_scraper import get_asins_from_libri
from src.scraper.videogiochi_scraper import get_asins_from_videogiochi
from src.scraper.alimentari_scraper import get_asins_from_alimentari
from src.scraper.animali_scraper import get_asins_from_animali
from src.scraper.all_scraper import get_asins_from_all
from src.scraper.offerte_giorno_scraper import get_offerte_giorno_asins
from src.scraper.product_scraper import extract_product_info

CATEGORY_SOURCE_MAP = {
    "cat_abbigliamento": get_asins_from_abbigliamento,
    "cat_elettronica":   get_asins_from_electronics,
    "cat_deals":         get_deals_asins,
    "cat_casa_cucina":   get_asins_from_casa_cucina,
    "cat_bellezza":      get_asins_from_bellezza,
    "cat_sport":         get_asins_from_sport,
    "cat_giocattoli":    get_asins_from_giocattoli,
    "cat_faidate":       get_asins_from_faidate,
    "cat_auto_moto":     get_asins_from_auto_moto,
    "cat_libri":         get_asins_from_libri,
    "cat_videogiochi":   get_asins_from_videogiochi,
    "cat_alimentari":    get_asins_from_alimentari,
    "cat_animali":       get_asins_from_animali,
    "cat_all":           get_asins_from_all,
    "cat_goldbox":       get_offerte_giorno_asins
}

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WATCHLIST_CHECK_INTERVAL = WATCHLIST_CHECK_INTERVAL_SECONDS
REFILL_CHECK_INTERVAL = REFILL_CHECK_INTERVAL_SECONDS

def _update_last_buffer_clear(user_id: int, timestamp: float):
    users = load_user_data()
    uid = str(user_id)
    users.setdefault(uid, {})
    users[uid]["last_buffer_clear"] = timestamp
    save_user_data(users)


def schedule_refill_in_thread(user_id: int, category: str, src_fn):
    try:
        asyncio.get_event_loop().create_task(
            asyncio.to_thread(refill_buffer_for_user, user_id, category, src_fn)
        )
    except RuntimeError:
        def _worker():
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    asyncio.to_thread(refill_buffer_for_user, user_id, category, src_fn)
                )
            finally:
                loop.close()
        import threading
        threading.Thread(target=_worker, daemon=True).start()


# ==========================
# AUTOPOST LOOP PRINCIPALE
# ==========================
async def autopost_loop(app):
    """
    V2.7 pulita:
    - sceglie le migliori offerte dentro ogni categoria attiva dell'utente;
    - pubblica il numero di offerte scelto dall'utente PER OGNI CATEGORIA;
      esempio: 2 offerte per ciclo + 2 categorie attive = fino a 4 post totali;
    - rispetta intervallo, orari attivi, anti-duplicato e score minimo;
    - il refill riempie il buffer ma non pubblica subito.
    """

    def _offer_score(prod, category_key: str) -> float:
        try:
            return score_super_offer(prod, category=category_key)
        except Exception:
            logger.exception(f"[AUTOPOST] Errore score asin={getattr(prod, 'asin', 'N/D')}")
            return -9999.0

    def _publish_instagram_sync(insta_cfg, user_id: int, prod, img_path: str) -> bool:
        try:
            ig_img = prepare_for_instagram(img_path)
            image_url = upload_to_cdn(ig_img)
            affiliate = generate_affiliate_link(user_id, prod.asin)

            old_price = getattr(prod, "old_price", None) or "N/D"
            discount = getattr(prod, "discount", None) or 0
            caption_ig = (
                f"{prod.title}\n"
                f"💰 {prod.price}€ invece di {old_price}€ (-{discount}%)\n"
                f"{affiliate}"
            )

            res_create = requests.post(
                f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media",
                params={
                    "image_url": image_url,
                    "caption": caption_ig,
                    "access_token": insta_cfg["access_token"],
                },
                verify=False,
                timeout=20,
            )
            logger.info(f"[AUTOPOST][IG] create_media status={res_create.status_code} body={res_create.text}")
            data_create = res_create.json()
            cid = data_create.get("id")
            if not cid:
                logger.error(f"[AUTOPOST][IG] Creazione media fallita: {data_create}")
                return False

            res_publish = requests.post(
                f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media_publish",
                params={"creation_id": cid, "access_token": insta_cfg["access_token"]},
                verify=False,
                timeout=20,
            )
            logger.info(f"[AUTOPOST][IG] media_publish status={res_publish.status_code} body={res_publish.text}")
            return res_publish.status_code == 200
        except Exception:
            logger.exception("[AUTOPOST][IG] Errore pubblicazione Instagram")
            return False

    while True:
        try:
            logger.info("[AUTOPOST] ▶️ Inizio ciclo V2...")
            users = load_user_data()

            for uid_str, data in users.items():
                try:
                    user_id = int(uid_str)
                except Exception:
                    continue

                now = time.time()

                next_post = data.get("next_post", 0)
                if now < next_post:
                    logger.debug(f"[AUTOPOST] Utente {user_id}: prossimo post tra {round(next_post - now)}s")
                    continue

                if not is_within_active_schedule(user_id):
                    next_dt = next_active_datetime(user_id)
                    if next_dt is not None:
                        users_latest = load_user_data()
                        users_latest.setdefault(uid_str, {})
                        users_latest[uid_str]["next_post"] = next_dt.timestamp()
                        save_user_data(users_latest)
                        logger.info(
                            f"[AUTOPOST] Utente {user_id} fuori orario ({format_schedule_status(user_id)}). "
                            f"Riprovo alle {next_dt.strftime('%d/%m %H:%M')}."
                        )
                    else:
                        logger.info(f"[AUTOPOST] Utente {user_id} fuori orario, salto.")
                    continue

                categories = data.get("categories", [])
                if not categories:
                    continue

                try:
                    offers_per_category = get_user_offers_per_cycle(user_id)
                except Exception:
                    offers_per_category = max(1, int(MAX_OFFERS_PER_USER_CYCLE))

                # V2.8: il numero impostato dall'utente vale PER OGNI CATEGORIA.
                # Se una categoria ha buffer pieno ma nessuna offerta valida, il buffer viene
                # eliminato e viene avviata subito una nuova scansione/refill. Gli ASIN scartati
                # finiscono in quarantena temporanea per evitare di ricaricare sempre gli stessi.
                cleanup_expired_rejections()
                selected = []
                seen_asins_user_cycle: set[str] = set()
                stale_categories_to_refill: set[str] = set()

                for cat in categories:
                    try:
                        buffer_products = load_buffered_products(user_id, cat)

                        if not buffer_products or len(buffer_products) < 3:
                            src_fn = CATEGORY_SOURCE_MAP.get(cat)
                            if src_fn:
                                logger.info(f"[AUTOPOST] Buffer scarso {user_id}_{cat}, refill in background")
                                schedule_refill_in_thread(user_id, cat, src_fn)

                        category_candidates = []
                        rejected_low_score_asins: list[str] = []
                        blocked_or_duplicate_count = 0

                        for prod in buffer_products:
                            asin = str(getattr(prod, "asin", "") or "").strip().upper()
                            if not asin or asin in seen_asins_user_cycle:
                                blocked_or_duplicate_count += 1
                                continue
                            if not is_valid_for_resend(user_id, asin):
                                blocked_or_duplicate_count += 1
                                continue

                            score = _offer_score(prod, cat)
                            if score < MIN_OFFER_SCORE:
                                rejected_low_score_asins.append(asin)
                                logger.info(
                                    f"[AUTOPOST] Skip {asin}: score={score} < {MIN_OFFER_SCORE} | "
                                    f"{build_offer_debug_summary(prod, category=cat)}"
                                )
                                continue

                            category_candidates.append((score, cat, prod))

                        category_candidates.sort(key=lambda x: x[0], reverse=True)

                        picked_for_category = 0
                        for score, picked_cat, picked_prod in category_candidates:
                            asin = str(getattr(picked_prod, "asin", "") or "").strip().upper()
                            if not asin or asin in seen_asins_user_cycle:
                                continue
                            selected.append((score, picked_cat, picked_prod))
                            seen_asins_user_cycle.add(asin)
                            picked_for_category += 1
                            if picked_for_category >= offers_per_category:
                                break

                        logger.info(
                            f"[AUTOPOST] User={user_id} cat={cat}: "
                            f"selezionate {picked_for_category}/{offers_per_category} offerte valide"
                        )

                        if buffer_products and picked_for_category == 0 and not category_candidates:
                            logger.warning(
                                f"[AUTOPOST] Buffer {user_id}_{cat} contiene {len(buffer_products)} prodotti "
                                f"ma nessun candidato valido. Elimino buffer e avvio nuova scansione."
                            )
                            mark_rejected_asins(user_id, cat, rejected_low_score_asins, reason="autopost_low_score")
                            delete_buffer_file(user_id, cat)
                            stale_categories_to_refill.add(cat)

                    except Exception:
                        logger.exception(f"[AUTOPOST] Errore lettura categoria {cat} user {user_id}")

                for cat in stale_categories_to_refill:
                    src_fn = CATEGORY_SOURCE_MAP.get(cat)
                    if src_fn:
                        schedule_refill_in_thread(user_id, cat, src_fn)

                if not selected:
                    if stale_categories_to_refill:
                        logger.info(
                            f"[AUTOPOST] Nessun candidato valido per user {user_id}. "
                            f"Buffer ripuliti e refill avviato per: {', '.join(sorted(stale_categories_to_refill))}"
                        )
                    else:
                        logger.info(f"[AUTOPOST] Nessun candidato valido per user {user_id}")
                    continue

                logger.info(
                    f"[AUTOPOST] User={user_id}: ciclo con {len(categories)} categorie attive, "
                    f"{offers_per_category} offerte per categoria, totale selezionate={len(selected)}"
                )
                published_any = False

                for score, cat, prod in selected:
                    img = None
                    telegram_ok = False
                    facebook_ok = False
                    instagram_ok = False

                    try:
                        category_name = getattr(prod, "category", None) or cat.replace("cat_", "").replace("_", " ").title()
                        logger.info(
                            f"[AUTOPOST] Pubblico candidato {prod.asin} user={user_id} cat={cat} score={score} | "
                            f"{build_offer_debug_summary(prod, category=cat)}"
                        )

                        img = await asyncio.to_thread(
                            crea_immagine_offerta_da_url,
                            prod.image,
                            prod.price,
                            prod.discount,
                            prod.old_price,
                            prod.asin,
                        )

                        text, markup = build_offer_message(prod, user_id, category_name=category_name)
                        targets = data.get("telegram_channels") or [user_id]

                        for ch in targets:
                            try:
                                with open(img, "rb") as fh:
                                    await app.bot.send_photo(
                                        chat_id=ch,
                                        photo=fh,
                                        caption=text,
                                        reply_markup=markup,
                                        parse_mode=ParseMode.MARKDOWN_V2,
                                    )
                                logger.info(f"[AUTOPOST] Inviato Telegram a {ch} asin={prod.asin}")
                                telegram_ok = True
                                await asyncio.sleep(random.uniform(1.5, 3.5))
                            except RetryAfter as e:
                                wait = int(e.retry_after) + 2
                                logger.warning(f"[AUTOPOST] FloodWait {wait}s per {ch}")
                                await asyncio.sleep(wait)
                            except (TimedOut, NetworkError) as e:
                                logger.warning(f"[AUTOPOST] Timeout rete Telegram: {e}")
                                await asyncio.sleep(5)
                            except Exception:
                                logger.exception(f"[AUTOPOST] Errore invio Telegram a {ch}")

                        if ENABLE_FACEBOOK_POSTING:
                            try:
                                facebook_ok = await asyncio.to_thread(publish_offer_to_facebook, user_id, prod, img)
                            except Exception:
                                logger.exception("[AUTOPOST] Errore Facebook")

                        if ENABLE_INSTAGRAM_POSTING:
                            insta_cfg = data.get("instagram_config", {})
                            if insta_cfg.get("access_token") and insta_cfg.get("instagram_business_id"):
                                instagram_ok = await asyncio.to_thread(_publish_instagram_sync, insta_cfg, user_id, prod, img)

                        if telegram_ok or facebook_ok or instagram_ok:
                            published_any = True
                            remove_posted_asins(user_id, cat, [prod])
                            logger.info(
                                f"[AUTOPOST] Pubblicazione completata user={user_id} asin={prod.asin} "
                                f"telegram={telegram_ok} facebook={facebook_ok} instagram={instagram_ok}"
                            )
                        else:
                            logger.warning(f"[AUTOPOST] Nessuna piattaforma ha pubblicato asin={prod.asin} user={user_id}")

                    except Exception:
                        logger.exception(f"[AUTOPOST] Errore pubblicazione asin={getattr(prod, 'asin', 'N/D')}")
                    finally:
                        try:
                            if img and os.path.exists(img):
                                os.remove(img)
                        except Exception:
                            logger.exception("[AUTOPOST] Errore cleanup immagine")

                if published_any:
                    interval = data.get("post_interval", get_user_post_interval(user_id))
                    users_latest = load_user_data()
                    users_latest.setdefault(uid_str, {})
                    users_latest[uid_str]["next_post"] = time.time() + int(interval) * 60
                    save_user_data(users_latest)
                    logger.info(f"[AUTOPOST] Prossimo post user={user_id} tra {interval} minuti")

        except Exception:
            logger.exception("[AUTOPOST] Errore generale loop V2")

        await asyncio.sleep(AUTOPOST_SLEEP_SECONDS)

# ==========================
# REFILL LOOP (periodico)
# ==========================
async def refill_loop():
    """
    Loop che controlla i buffer utente e chiama refill_buffer_for_user quando serve.
    refill_buffer_for_user viene chiamata in thread (non blocca il loop).
    """
    while True:
        try:
            logger.info("[REFILL_LOOP] ▶️ Inizio ciclo di controllo refill")
            users = load_user_data()
            for uid_str, data in users.items():
                user_id = int(uid_str)
                for cat in data.get("categories", []):
                    try:
                        buffer_products = load_buffered_products(user_id, cat)
                        if not buffer_products or needs_refill(user_id, cat):
                            src_fn = CATEGORY_SOURCE_MAP.get(cat)
                            if src_fn:
                                logger.info(f"[REFILL_LOOP] 🔄 Avvio refill per {user_id}_{cat} (buffer attualmente {len(buffer_products) if buffer_products else 0})")
                                schedule_refill_in_thread(user_id, cat, src_fn)
                    except Exception:
                        logger.exception(f"[REFILL_LOOP] Errore refill controllo {user_id}_{cat}")
        except Exception:
            logger.exception("[REFILL_LOOP] Errore generale loop refill")
        await asyncio.sleep(REFILL_CHECK_INTERVAL)


# ==========================
# WATCHLIST LOOP
# ==========================
async def watchlist_loop(app):
    while True:
        try:
            users = load_user_data()

            for uid_str, data in users.items():
                user_id = int(uid_str)
                watchlist = data.get("watchlist", [])

                if not watchlist:
                    continue

                updated_watchlist = list(watchlist)

                for item in watchlist[:]:
                    asin = item.get("asin")
                    threshold = item.get("threshold")

                    try:
                        if not asin or threshold is None:
                            logger.warning(f"[WATCHLIST] ⚠️ Voce watchlist non valida per user {user_id}: {item}")
                            continue

                        prod = extract_product_info(asin)

                        if not prod or not getattr(prod, "price", None) or prod.price == "N/D":
                            logger.info(f"[WATCHLIST] ℹ️ Prezzo non disponibile per asin {asin} user {user_id}")
                            continue

                        current = parse_price(prod.price)
                        if current is None:
                            logger.info(f"[WATCHLIST] ℹ️ Impossibile convertire prezzo asin {asin} user {user_id}: {prod.price}")
                            continue

                        threshold_value = parse_price(threshold)
                        if threshold_value is None:
                            logger.warning(f"[WATCHLIST] ⚠️ Soglia non valida asin {asin} user {user_id}: {threshold}")
                            continue

                        if current <= threshold_value:
                            txt = (
                                f"🔔 *Price alert!*\n"
                                f"{prod.title}\n\n"
                                f"💰 Prezzo attuale: *{current:.2f}€*\n"
                                f"🎯 Soglia impostata: *{threshold_value:.2f}€*\n"
                                f"https://www.amazon.it/dp/{asin}"
                            )

                            await app.bot.send_message(
                                chat_id=user_id,
                                text=txt,
                                parse_mode=ParseMode.MARKDOWN
                            )

                            logger.info(
                                f"[WATCHLIST] ✅ Alert inviato per asin {asin} user {user_id} "
                                f"(current={current:.2f} threshold={threshold_value:.2f})"
                            )

                            updated_watchlist = [x for x in updated_watchlist if x.get("asin") != asin]

                    except Exception:
                        logger.exception(f"[WATCHLIST] Errore gestione asin {asin} per user {user_id}")

                users[uid_str]["watchlist"] = updated_watchlist

            save_user_data(users)

        except Exception:
            logger.exception("[WATCHLIST] Errore generale loop watchlist")

        await asyncio.sleep(WATCHLIST_CHECK_INTERVAL)


# ==========================
# INVIO SINGOLA OFFERTA (manuale / immediata)
# ==========================
async def send_single_offer(app, user_id: int, prod, img_path: str):
    """
    Manda immediatamente su Telegram, Facebook e Instagram
    l'offerta 'prod' con l'immagine già generata in 'img_path'.
    app può essere None: in quel caso il Bot viene creato dal token d'ambiente.
    """

    from telegram import Bot
    from telegram.constants import ParseMode
    from telegram.error import RetryAfter, TimedOut, NetworkError

    from src.database.user_data_manager import load_user_data
    from src.utils.product import build_offer_message
    from src.utils.image_prep import prepare_for_instagram
    from src.utils.your_cdn_uploader import upload_to_cdn
    from src.utils.shortlink_generator import generate_affiliate_link
    from src.utils.facebook import publish_offer_to_facebook
    from src.utils.instagram_integration import API_VERSION

    def _publish_instagram_sync(insta_cfg, user_id: int, prod, img_path: str) -> bool:
        try:
            ig_img = prepare_for_instagram(img_path)
            image_url = upload_to_cdn(ig_img)
            affiliate = generate_affiliate_link(user_id, prod.asin)

            old_price = getattr(prod, "old_price", None) or "N/D"
            discount = getattr(prod, "discount", None) or 0

            caption_ig = (
                f"{prod.title}\n"
                f"💰 {prod.price}€ invece di {old_price}€ (-{discount}%)\n"
                f"{affiliate}"
            )

            res_create = requests.post(
                f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media",
                params={
                    "image_url": image_url,
                    "caption": caption_ig,
                    "access_token": insta_cfg["access_token"]
                },
                verify=False,
                timeout=20
            )

            logger.info(f"[SEND_SINGLE][IG] create_media status={res_create.status_code} body={res_create.text}")
            data_create = res_create.json()

            cid = data_create.get("id")
            if not cid:
                logger.error(f"[SEND_SINGLE][IG] ❌ Creazione media fallita: {data_create}")
                return False

            res_publish = requests.post(
                f"https://graph.facebook.com/{API_VERSION}/{insta_cfg['instagram_business_id']}/media_publish",
                params={
                    "creation_id": cid,
                    "access_token": insta_cfg["access_token"]
                },
                verify=False,
                timeout=20
            )

            logger.info(f"[SEND_SINGLE][IG] media_publish status={res_publish.status_code} body={res_publish.text}")

            if res_publish.status_code == 200:
                return True

            return False

        except Exception:
            logger.exception("[SEND_SINGLE][IG] Errore pubblicazione Instagram")
            return False

    text = ""
    markup = None
    telegram_ok = False
    facebook_ok = False
    instagram_ok = False

    try:
        text, markup = build_offer_message(
            prod,
            user_id,
            category_name=getattr(prod, "category", None)
        )
    except Exception:
        logger.exception("[SEND_SINGLE] Errore costruzione caption")
        text, markup = ("", None)

    # Se app è None crea Bot base
    if app is None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.error("[SEND_SINGLE] TELEGRAM_BOT_TOKEN non definito in env")
            return
        app = type("DummyApp", (), {})()
        app.bot = Bot(token=token)

    try:
        data = load_user_data().get(str(user_id), {})
        targets = data.get("telegram_channels") or [user_id]

        # Telegram
        for ch in targets:
            try:
                with open(img_path, "rb") as photo:
                    await app.bot.send_photo(
                        chat_id=ch,
                        photo=photo,
                        caption=text,
                        reply_markup=markup,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                logger.info(f"[SEND_SINGLE] ✅ Foto inviata a {ch}")
                telegram_ok = True
                await asyncio.sleep(random.uniform(1.5, 3.5))

            except RetryAfter as e:
                wait = int(e.retry_after) + 2
                logger.warning(f"[SEND_SINGLE] ⚠️ FloodWait {wait}s per {ch}")
                await asyncio.sleep(wait)

            except (TimedOut, NetworkError) as e:
                logger.warning(f"[SEND_SINGLE] ⚠️ Timeout rete Telegram: {e}")
                await asyncio.sleep(5)

            except Exception:
                logger.exception(f"[SEND_SINGLE] Errore invio a {ch}")

        # Facebook
        try:
            facebook_ok = await asyncio.to_thread(
                publish_offer_to_facebook,
                user_id,
                prod,
                img_path
            )
            if facebook_ok:
                logger.info(f"[SEND_SINGLE] ✅ Pubblicato su Facebook per user {user_id}")
        except Exception:
            logger.exception("[SEND_SINGLE] Errore publish_to_facebook")

        # Instagram
        try:
            insta_cfg = data.get("instagram_config", {})
            if insta_cfg.get("access_token") and insta_cfg.get("instagram_business_id"):
                instagram_ok = await asyncio.to_thread(
                    _publish_instagram_sync,
                    insta_cfg,
                    user_id,
                    prod,
                    img_path
                )
                if instagram_ok:
                    logger.info(f"[SEND_SINGLE] ✅ Pubblicato su Instagram per user {user_id}")
        except Exception:
            logger.exception("[SEND_SINGLE] Errore Instagram")

        if telegram_ok or facebook_ok or instagram_ok:
            logger.info(
                f"[SEND_SINGLE] ✅ Completato invio offerta user={user_id} "
                f"(telegram={telegram_ok}, facebook={facebook_ok}, instagram={instagram_ok})"
            )
        else:
            logger.warning(
                f"[SEND_SINGLE] ⚠️ Nessuna piattaforma ha pubblicato l'offerta "
                f"user={user_id} asin={getattr(prod, 'asin', 'N/D')}"
            )

    finally:
        try:
            if img_path and os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            logger.exception("[SEND_SINGLE] Errore rimozione file immagine")


# ==========================
# INIZIALIZZAZIONE ALL'AVVIO
# ==========================
def initialize_next_post_for_users():
    users = load_user_data()
    now = time.time()

    for uid_str, data in users.items():
        try:
            user_id = int(uid_str)
            interval = data.get("post_interval", 15)

            current_next_post = data.get("next_post", 0)
            if not current_next_post or current_next_post < 0:
                users[uid_str]["next_post"] = now + interval * 60
                logger.info(f"[STARTUP] ⏲️ Scheduling next_post per {user_id} tra {interval}m")
            else:
                logger.info(f"[STARTUP] ℹ️ next_post già presente per {user_id}: {current_next_post}")

            categories = data.get("categories", [])
            for cat in categories:
                if not load_buffered_products(user_id, cat):
                    logger.info(f"[STARTUP] Buffer vuoto per {user_id}_{cat}, refill iniziale")
                    src_fn = CATEGORY_SOURCE_MAP.get(cat)
                    if src_fn:
                        try:
                            refill_buffer_for_user(user_id, cat, src_fn)
                        except Exception:
                            logger.exception(f"[STARTUP] Errore refill {user_id}_{cat}")

        except Exception:
            logger.exception("[STARTUP] Errore inizializzazione per user")

    save_user_data(users)

# ==========================
# START SCHEDULER (avvia i 3 loop principali)
# ==========================
async def start_scheduler(app):
    """
    Avvia i loop principali dello scheduler:
    - autopost_loop
    - refill_loop
    - watchlist_loop

    Esegue prima l'inizializzazione dei next_post e dei refill iniziali.
    Se uno dei loop va in errore, annulla anche gli altri e rilancia l'eccezione.
    """
    logger.info("[START_SCHEDULER] ▶️ Avvio scheduler...")

    await asyncio.sleep(1)

    try:
        initialize_next_post_for_users()
        logger.info("[START_SCHEDULER] ✅ Inizializzazione completata.")
    except Exception:
        logger.exception("[START_SCHEDULER] ❌ Errore durante initialize_next_post_for_users()")
        raise

    autopost_runner = asyncio.create_task(autopost_loop(app), name="autopost_loop")
    refill_runner = asyncio.create_task(refill_loop(), name="refill_loop")
    watchlist_runner = asyncio.create_task(watchlist_loop(app), name="watchlist_loop")

    tasks = [autopost_runner, refill_runner, watchlist_runner]

    logger.info("[START_SCHEDULER] ✅ Scheduler pronto. Loop principali avviati.")

    try:
        await asyncio.gather(*tasks)
    except Exception:
        logger.exception("[START_SCHEDULER] ❌ Errore in uno dei loop principali. Arresto di tutti i task...")

        for task in tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        raise
