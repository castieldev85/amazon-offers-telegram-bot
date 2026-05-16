import logging

from src.scraper.category_pagination import collect_asins_from_category

logger = logging.getLogger(__name__)


def get_asins_from_auto_moto(min_discount_label="3", max_scrolls=None, max_pages=None):
    return collect_asins_from_category(
        url='https://www.amazon.it/s?k=offerte+auto+moto',
        label='auto e moto',
        min_discount_label=min_discount_label,
        max_scrolls=max_scrolls,
        max_pages=max_pages,
    )
