import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.buffer.buffer_manager import add_products_to_buffer, count_products_in_buffer
from src.configs.settings import ENABLE_INSTANT_POST_AFTER_REFILL, MIN_OFFER_SCORE, REFILL_BATCH_SIZE
from src.database.user_data_manager import get_user_min_discount
from src.scraper.product_scraper import extract_product_info
from src.utils.amazon_api_helper import search_products_by_keyword
from src.utils.database_builder import get_last_posted_date, is_valid_for_resend
from src.utils.offer_scorer import score_super_offer, parse_price
from src.buffer.rejected_offers import is_rejected_asin

logger = logging.getLogger(__name__)

ACTIVE_REFILLS: dict[str, bool] = {}
ACTIVE_REFILLS_LOCK = threading.Lock()

CATEGORY_BUFFER_LIMITS = {
    "cat_elettronica": 200,
    "cat_deals": 200,
    "cat_abbigliamento": 200,
    "cat_casa_cucina": 200,
    "cat_bellezza": 200,
    "cat_sport": 200,
    "cat_giocattoli": 200,
    "cat_faidate": 200,
    "cat_auto_moto": 200,
    "cat_libri": 200,
    "cat_videogiochi": 200,
    "cat_alimentari": 200,
    "cat_animali": 200,
    "cat_all": 200,
    "cat_goldbox": 200,
}

KEYWORD_MAP = {
    "cat_elettronica": "offerte elettronica",
    "cat_casa_cucina": "offerte casa cucina",
    "cat_bellezza": "offerte bellezza",
    "cat_sport": "offerte sport fitness",
    "cat_giocattoli": "offerte giocattoli",
    "cat_auto_moto": "accessori auto",
    "cat_abbigliamento": "offerte abbigliamento",
    "cat_deals": "offerte amazon",
    "cat_all": "offerte sconti",
    "cat_faidate": "offerte fai da te",
    "cat_libri": "libri e manuali",
    "cat_videogiochi": "videogiochi sconti",
    "cat_alimentari": "spesa alimentari offerte",
    "cat_animali": "prodotti per animali",
    "cat_goldbox": "amazon gold box",
}


def _safe_asin(asin: str) -> str:
    return str(asin or "").strip().upper()


def _dedupe_asins(asins: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for asin in asins or []:
        asin = _safe_asin(asin)
        if not asin or asin in seen:
            continue
        seen.add(asin)
        out.append(asin)
    return out


def _source_asins(category_code: str, asin_source_fn, min_discount: int, max_products: int) -> list[str]:
    try:
        all_asins = asin_source_fn() or []
    except TypeError:
        all_asins = asin_source_fn(category_code) or []

    all_asins = _dedupe_asins(all_asins)
    logger.info(f"[REFILL] ASIN iniziali trovati per {category_code}: {len(all_asins)}")

    if len(all_asins) < 10:
        kw = KEYWORD_MAP.get(category_code, "offerte amazon")
        try:
            extra_asins = search_products_by_keyword(
                kw,
                min_discount=min_discount,
                max_price=30,
                item_count=20,
            )
            logger.info(f"[REFILL] PA-API trovati {len(extra_asins)} ASIN extra keyword={kw!r}")
            all_asins.extend(extra_asins)
        except Exception:
            logger.exception(f"[REFILL] Errore fallback PA-API per {category_code}")

    return _dedupe_asins(all_asins)[:max_products]


def refill_buffer_for_user(user_id: int, category_code: str, asin_source_fn):
    key = f"{user_id}_{category_code}"

    with ACTIVE_REFILLS_LOCK:
        if ACTIVE_REFILLS.get(key, False):
            logger.warning(f"[REFILL] Refill già in corso per {key}, salto ciclo.")
            return
        ACTIVE_REFILLS[key] = True

    try:
        current_count = count_products_in_buffer(user_id, category_code)
        max_products = CATEGORY_BUFFER_LIMITS.get(category_code, 30)

        logger.info(f"[REFILL] Buffer {key}: {current_count}/{max_products}")
        if current_count >= max_products:
            logger.info(f"[REFILL] Buffer già pieno per {key}")
            return

        min_discount = get_user_min_discount(user_id)
        selected_asins = _source_asins(category_code, asin_source_fn, min_discount, max_products)
        final_products = []

        def process(asin: str):
            asin = _safe_asin(asin)
            if not asin:
                return None

            if is_rejected_asin(user_id, category_code, asin):
                logger.info(f"[REFILL] Skip {asin}: in quarantena qualità per {category_code}")
                return None

            if not is_valid_for_resend(user_id, asin):
                last_posted_date = get_last_posted_date(user_id, asin)
                logger.info(f"[REFILL] Skip {asin}: già pubblicato il {last_posted_date}")
                return None

            try:
                product = extract_product_info(asin)
            except Exception:
                logger.exception(f"[REFILL] Errore extract_product_info ASIN={asin}")
                return None

            if not product:
                return None

            price_num = parse_price(getattr(product, "price", None)) or 0.0
            discount = float(getattr(product, "discount", 0) or 0)

            if price_num < 0.5 or discount >= 100:
                logger.info(f"[REFILL] Prezzo/sconto sospetto: {asin} price={product.price} discount={product.discount}")
                return None

            score = score_super_offer(product, category=category_code)
            if discount >= min_discount or score >= MIN_OFFER_SCORE:
                logger.info(f"[REFILL] OK {asin} discount={discount}% score={score} price={product.price}€")
                return product

            logger.info(f"[REFILL] Skip {asin}: discount={discount}% < {min_discount}% e score={score} < {MIN_OFFER_SCORE}")
            return None

        batch_size = max(1, int(REFILL_BATCH_SIZE))
        for i in range(0, len(selected_asins), batch_size):
            current_batch = selected_asins[i:i + batch_size]
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = [executor.submit(process, asin) for asin in current_batch]
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        final_products.append(result)

        add_products_to_buffer(user_id, category_code, final_products)
        logger.info(f"[REFILL] Aggiunti {len(final_products)} prodotti nel buffer {key}")

        if ENABLE_INSTANT_POST_AFTER_REFILL and final_products:
            logger.warning(
                "[REFILL] ENABLE_INSTANT_POST_AFTER_REFILL=True: il refill pubblicherà subito. "
                "Per V2 pulita è consigliato lasciarlo False."
            )
            try:
                import asyncio
                from src.autoposting import send_single_offer
                from src.utils.image_builder import crea_immagine_offerta_da_url

                first_prod = max(final_products, key=lambda p: score_super_offer(p, category=category_code))
                img_path = crea_immagine_offerta_da_url(
                    url=first_prod.image,
                    prezzo=first_prod.price,
                    sconto=first_prod.discount,
                    vecchio_prezzo=first_prod.old_price,
                    asin=first_prod.asin,
                )
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(send_single_offer(None, user_id, first_prod, img_path))
                finally:
                    loop.close()
            except Exception:
                logger.exception(f"[REFILL] Errore instant post per {key}")

    finally:
        with ACTIVE_REFILLS_LOCK:
            ACTIVE_REFILLS[key] = False
        logger.info(f"[REFILL] Refill completato per {key}")
