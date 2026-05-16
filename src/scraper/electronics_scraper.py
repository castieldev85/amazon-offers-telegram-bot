import logging

from src.scraper.category_pagination import collect_asins_from_category

logger = logging.getLogger(__name__)


def get_asins_from_electronics(min_discount_label="3", max_scrolls=None, max_pages=None):
    return collect_asins_from_category(
        url='https://www.amazon.it/offerte-del-giorno-elettronica/s?k=offerte+del+giorno+elettronica',
        label='elettronica',
        min_discount_label=min_discount_label,
        max_scrolls=max_scrolls,
        max_pages=max_pages,
    )
