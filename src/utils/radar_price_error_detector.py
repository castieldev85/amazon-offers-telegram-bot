import logging
import time

from src.utils.amazon_api_helper import fetch_product_details_from_api, search_products_by_keyword
from src.utils.extract_product_info_selenium import extract_product_info_selenium
from src.utils.offer_scorer import parse_price, score_super_offer
from src.utils.product import Product

logger = logging.getLogger(__name__)


def search_asins_by_keyword(keywords: str, max_results: int = 20, min_discount: int = 50) -> list[str]:
    """Cerca ASIN tramite PA-API usando le credenziali configurate nel file .env."""
    return search_products_by_keyword(
        keywords=keywords or "offerte amazon",
        min_discount=min_discount,
        max_price=None,
        item_count=min(10, max_results),
        page=1,
    )


def scan_price_errors(keywords: str = "", min_discount: int = 50, max_results: int = 20) -> list[Product]:
    """
    Scansiona offerte molto forti usando PA-API + fallback Selenium.
    Non contiene più credenziali hardcoded: legge tutto da .env tramite amazon_api_helper.
    """
    detected_products: list[Product] = []
    asin_list = search_asins_by_keyword(keywords, max_results=max_results, min_discount=min_discount)

    for asin in asin_list[:max_results]:
        try:
            prod = fetch_product_details_from_api(asin)
            if not prod or not parse_price(getattr(prod, "price", None)):
                prod = extract_product_info_selenium(f"https://www.amazon.it/dp/{asin}")

            if not prod:
                continue

            price_val = parse_price(getattr(prod, "price", None))
            old_price_val = parse_price(getattr(prod, "old_price", None))
            if not price_val:
                continue

            discount = 0
            if old_price_val and old_price_val > price_val:
                discount = int(round((old_price_val - price_val) / old_price_val * 100))
                prod.discount = max(int(getattr(prod, "discount", 0) or 0), discount)

            if discount >= min_discount or score_super_offer(prod) >= 90:
                detected_products.append(prod)

        except Exception:
            logger.exception(f"[RADAR] Errore scansione ASIN={asin}")
        finally:
            time.sleep(1)

    return detected_products
