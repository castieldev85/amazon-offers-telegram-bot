import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.database.user_data_manager import get_user_min_discount
from src.buffer.buffer_manager import add_products_to_buffer, count_products_in_buffer
from src.scraper.product_scraper import extract_product_info
from src.utils.database_builder import is_valid_for_resend, get_last_posted_date
from src.utils.amazon_api_helper import search_products_by_keyword
from src.utils.offer_scorer import parse_price, score_super_offer, build_offer_debug_summary

logger = logging.getLogger(__name__)

# 🔒 Dizionario globale refill
ACTIVE_REFILLS = {}
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
    "cat_goldbox": 200
}


def refill_buffer_for_user(user_id: int, category_code: str, asin_source_fn):
    key = f"{user_id}_{category_code}"

    with ACTIVE_REFILLS_LOCK:
        if ACTIVE_REFILLS.get(key, False):
            logger.warning(f"[REFILL] ⏳ Refill già in corso per {key}, salto ciclo.")
            return
        ACTIVE_REFILLS[key] = True

    try:
        current_count = count_products_in_buffer(user_id, category_code)
        max_products = CATEGORY_BUFFER_LIMITS.get(category_code, 30)

        logger.info(f"[REFILL] Buffer {user_id}_{category_code}: {current_count}/{max_products}")

        if current_count >= max_products:
            logger.info(f"[REFILL] Buffer già pieno per {user_id}_{category_code}")
            return

        min_discount = get_user_min_discount(user_id)
        logger.info(f"[REFILL] Avvio refill (min sconto {min_discount}%)")

        all_asins = asin_source_fn() or []
        logger.info(f"[DEBUG] ASIN iniziali trovati: {len(all_asins)}")

        # 🔍 FALLBACK automatico se pochi ASIN
        if len(all_asins) < 10:
            keyword_map = {
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
                "cat_goldbox": "amazon gold box"
            }

            kw = keyword_map.get(category_code, "offerte amazon")
            extra_asins = search_products_by_keyword(
                kw,
                min_discount=min_discount,
                max_price=30,
                item_count=20
            )

            logger.info(f"[REFILL] 🔍 PA-API trovati {len(extra_asins)} nuovi ASIN con keyword '{kw}'")
            all_asins.extend(extra_asins)

        # Deduplica ASIN mantenendo l'ordine
        seen = set()
        deduped_asins = []
        for asin in all_asins:
            if asin and asin not in seen:
                seen.add(asin)
                deduped_asins.append(asin)

        # Analizza più ASIN del target buffer, così il filtro finale non resta troppo corto
        scan_limit = min(len(deduped_asins), max_products * 3)
        selected_asins = deduped_asins[:scan_limit]

        final_products = []

        def process(asin):
            logger.info(f"[THREAD] Analizzo ASIN: {asin}")

            product = extract_product_info(asin)
            if not product:
                return None

            logger.info(
                f"[DEBUG] {asin} → price={product.price}, old={product.old_price}, "
                f"discount={product.discount}, coupon={getattr(product, 'coupon_text', None)}"
            )

            if not is_valid_for_resend(user_id, asin):
                last_posted_date = get_last_posted_date(user_id, asin)
                logger.info(f"[REFILL] ❌ {asin} già pubblicato il {last_posted_date}")
                return None

            prezzo_num = parse_price(getattr(product, "price", None)) or 0.0

            try:
                discount_num = float(str(getattr(product, "discount", 0) or 0).replace(",", "."))
            except Exception:
                discount_num = 0.0

            # 🚫 prodotti anomali
            if prezzo_num < 0.5 or discount_num >= 100:
                logger.info(
                    f"[REFILL] 🚫 Prezzo sospetto: {getattr(product, 'title', 'N/D')} "
                    f"({getattr(product, 'price', 'N/D')} / {discount_num}%)"
                )
                return None

            # ✅ accetta solo con sconto >= min_discount
            if discount_num >= min_discount:
                try:
                    score = score_super_offer(product, category=category_code)
                except Exception:
                    score = 0.0

                logger.info(
                    f"[REFILL] ✅ {getattr(product, 'title', 'N/D')} | "
                    f"{discount_num}% | {prezzo_num}€ | score={score} | "
                    f"{build_offer_debug_summary(product, category=category_code)}"
                )
                return product

            logger.info(f"[REFILL] ❌ Sconto troppo basso: {discount_num}% < {min_discount}%")
            return None

        # ⚙️ esecuzione multi-thread
        batch_size = 2
        for i in range(0, len(selected_asins), batch_size):
            current_batch = selected_asins[i:i + batch_size]

            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = [executor.submit(process, asin) for asin in current_batch]

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            final_products.append(result)
                    except Exception:
                        logger.exception(f"[REFILL] Errore analisi asin in batch per {key}")

        # Ordina i prodotti migliori in testa al buffer
        try:
            final_products.sort(
                key=lambda p: score_super_offer(p, category=category_code),
                reverse=True
            )
        except Exception:
            logger.exception(f"[REFILL] Errore ordinamento prodotti per score in {key}")

        # Limita al numero di slot rimasti nel buffer
        remaining_slots = max_products - current_count
        if remaining_slots > 0:
            final_products = final_products[:remaining_slots]
        else:
            final_products = []

        add_products_to_buffer(user_id, category_code, final_products)
        logger.info(f"[REFILL] ➕ Aggiunti {len(final_products)} prodotti nel buffer {user_id}_{category_code}")

        # ❌ NESSUN POST IMMEDIATO DAL REFILL
        logger.info(f"[REFILL] ℹ️ Nessun post immediato: la pubblicazione è gestita solo da autopost_loop()")

    finally:
        with ACTIVE_REFILLS_LOCK:
            ACTIVE_REFILLS[key] = False
        logger.info(f"[REFILL] 🔓 Refill completato per {key}")
