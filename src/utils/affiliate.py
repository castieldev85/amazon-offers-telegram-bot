from src.utils.shortlink_generator import generate_affiliate_link, get_affiliate_tag, DEFAULT_TAG_ID


def get_affiliate_link(user_id: int, asin: str) -> str:
    """Compatibilità con il vecchio codice: usa il nuovo generatore centralizzato."""
    return generate_affiliate_link(user_id, asin)
