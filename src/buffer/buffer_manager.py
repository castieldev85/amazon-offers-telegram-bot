import json
import os
import logging
from typing import Iterable

from src.configs.settings import BUFFER_PATH
from src.scraper.product_scraper import extract_product_info
from src.utils.product import Product, extract_asin_from_url

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 200
MIN_BUFFER_SIZE = 5

os.makedirs(BUFFER_PATH, exist_ok=True)


def get_buffer_file(user_id: int, category_code: str) -> str:
    return os.path.join(BUFFER_PATH, f"{user_id}_{category_code}.json")


def _product_asin(product) -> str:
    asin = str(getattr(product, "asin", "") or "").strip().upper()
    if asin:
        return asin
    return (extract_asin_from_url(getattr(product, "link", "") or "") or "").upper()


def load_buffered_products(user_id: int, category_code: str) -> list[Product]:
    path = get_buffer_file(user_id, category_code)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                os.remove(path)
                return []
            data = json.loads(content)
            if not isinstance(data, list):
                os.remove(path)
                return []
        return [Product.from_dict(p) for p in data if isinstance(p, dict)]
    except Exception as e:
        logger.warning(f"[BUFFER] Errore caricamento buffer {user_id}_{category_code}: {e}. Elimino file corrotto.")
        try:
            os.remove(path)
        except Exception:
            pass
        return []


def save_buffered_products(user_id: int, category_code: str, products: list[Product]):
    os.makedirs(BUFFER_PATH, exist_ok=True)
    path = get_buffer_file(user_id, category_code)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in products], f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def count_products_in_buffer(user_id: int, category_code: str) -> int:
    return len(load_buffered_products(user_id, category_code))


def needs_refill(user_id: int, category_code: str) -> bool:
    return count_products_in_buffer(user_id, category_code) < MIN_BUFFER_SIZE


def add_products_to_buffer(user_id: int, category_code: str, new_products: list[Product]):
    current = load_buffered_products(user_id, category_code)
    all_products = current + [p for p in (new_products or []) if p]

    seen_asins: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[Product] = []

    for product in all_products:
        asin = _product_asin(product)
        title_key = str(getattr(product, "title", "") or "").strip().lower()
        key = asin or title_key
        if not key:
            continue
        if asin and asin in seen_asins:
            continue
        if not asin and title_key in seen_titles:
            continue
        if asin:
            seen_asins.add(asin)
        if title_key:
            seen_titles.add(title_key)
        unique.append(product)

    save_buffered_products(user_id, category_code, unique[:MAX_BUFFER_SIZE])


def remove_posted_asins(user_id: int, category_code: str, products: Iterable):
    buffer = load_buffered_products(user_id, category_code)
    posted_asins = {_product_asin(p) for p in products if _product_asin(p)}
    if not posted_asins:
        return
    new_buffer = [p for p in buffer if _product_asin(p) not in posted_asins]
    save_buffered_products(user_id, category_code, new_buffer)


def delete_buffer_file(user_id: int, category_code: str):
    filepath = get_buffer_file(user_id, category_code)
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"[BUFFER] Eliminato file buffer: {filepath}")


def delete_category_if_empty(user_id: int, category_code: str) -> bool:
    file_path = get_buffer_file(user_id, category_code)
    if not os.path.exists(file_path):
        return False

    products = load_buffered_products(user_id, category_code)
    if not products:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        logger.info(f"[BUFFER] Categoria {category_code} rimossa dal buffer per user {user_id}")
        return True
    return False


def delete_category_always(user_id: int, category_code: str) -> bool:
    path = get_buffer_file(user_id, category_code)
    if os.path.exists(path):
        try:
            os.remove(path)
            logger.info(f"[BUFFER] Categoria {category_code} rimossa dal buffer per user {user_id}")
            return True
        except Exception as e:
            logger.warning(f"[BUFFER] Errore eliminando categoria {category_code} per {user_id}: {e}")
    return False


def reset_and_refill_buffer(user_id: int, category_code: str, scraper_func):
    delete_buffer_file(user_id, category_code)

    try:
        asin_list = scraper_func() or []
    except TypeError:
        asin_list = scraper_func(category_code) or []
    except Exception as e:
        logger.exception(f"[BUFFER] Errore scraping per {category_code}: {e}")
        return []

    new_products = []
    for asin in asin_list:
        try:
            prod = extract_product_info(asin)
            if prod:
                new_products.append(prod)
        except Exception:
            logger.exception(f"[BUFFER] Errore su ASIN {asin}")

    if new_products:
        save_buffered_products(user_id, category_code, new_products)
        logger.info(f"[BUFFER] Buffer {category_code} resettato con {len(new_products)} prodotti")
    else:
        logger.info(f"[BUFFER] Nessun nuovo prodotto valido trovato per {category_code}")

    return new_products
