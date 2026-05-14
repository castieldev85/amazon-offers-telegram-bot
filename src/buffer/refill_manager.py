from concurrent.futures import ThreadPoolExecutor, as_completed
from src.database.user_data_manager import get_user_min_discount
from src.buffer.buffer_manager import add_products_to_buffer, needs_refill
from src.utils.product import Product
from src.scraper.product_scraper import extract_product_info
from src.utils.database_builder import is_valid_for_resend
import logging

logger = logging.getLogger(__name__)

from src.database.user_data_manager import get_user_days_limit, get_user_min_discount
from src.utils.database_builder import get_last_posted_date, is_valid_for_resend

def refill_buffer_for_user(user_id: int, category_code: str, asin_source_fn, max_products: int = 10):
    if not needs_refill(category_code):
        logger.info(f"[REFILL] Il buffer di {category_code} è già pieno.")
        return

    min_discount = get_user_min_discount(user_id)
    logger.info(f"[REFILL] Avvio refill per {category_code} (sconto minimo: {min_discount}%)")

    all_asins = asin_source_fn()
    selected_asins = all_asins[:max_products]

    final_products = []

    def process(asin):
        product = extract_product_info(asin)
        if not product:
            return None

        if not is_valid_for_resend(user_id, asin):
            days_limit = get_user_days_limit(user_id)
            last_posted_date = get_last_posted_date(user_id, asin)
            try:
                formatted_date = datetime.fromtimestamp(float(last_posted_date)).strftime("%Y-%m-%d %H:%M:%S")
            except:
                formatted_date = "❓ sconosciuta"
            logger.info(f"[REFILL] ⛔ {asin} già pubblicato da user {user_id} il {formatted_date}. Attesa: {days_limit} giorni.")
            return None

        if product.discount >= min_discount:
            logger.info(f"[REFILL] ✅ {product.title} | {product.discount}%")
            return product
        else:
            logger.info(f"[REFILL] ❌ {product.title} | Sconto {product.discount}% insufficiente")
            return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process, asin) for asin in selected_asins]
        for future in as_completed(futures):
            result = future.result()
            if result:
                final_products.append(result)


    add_products_to_buffer(category_code, final_products)
    logger.info(f"[REFILL] ➕ Aggiunti {len(final_products)} prodotti nel buffer {category_code}")
