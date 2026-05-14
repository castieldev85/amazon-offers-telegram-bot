import os
from dotenv import load_dotenv

load_dotenv()


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).replace(',', '.').strip())
    except Exception:
        return default


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "si", "sì", "on"}


def _as_admin_ids(value: str | None) -> set[int]:
    ids: set[int] = set()
    raw = (value or "").replace(";", ",")
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_IDS = _as_admin_ids(os.getenv("ADMIN_IDS", "1271567510"))

# Amazon / affiliate
DEFAULT_AFFILIATE_TAG = os.getenv("AMAZON_PARTNER_TAG", os.getenv("DEFAULT_AFFILIATE_TAG", "tuotag-21")).strip()

# Percorsi locali
BUFFER_PATH = os.getenv("BUFFER_PATH", "buffer_storage").strip()
USER_DATA_PATH = os.getenv("USER_DATA_PATH", "user_data.json").strip()
LINK_MAP_PATH = os.getenv("LINK_MAP_PATH", "link_map.json").strip()
PUBLISHED_DB_PATH = os.getenv("PUBLISHED_DB_PATH", "published_products.json").strip()
SEARCH_RESULTS_DIR = os.getenv("SEARCH_RESULTS_DIR", "search_results").strip()
REJECTED_OFFERS_PATH = os.getenv("REJECTED_OFFERS_PATH", "rejected_offers.json").strip()

# Qualità offerte / scheduler
MIN_OFFER_SCORE = _as_float(os.getenv("MIN_OFFER_SCORE"), 55.0)
MAX_OFFERS_PER_USER_CYCLE = _as_int(os.getenv("MAX_OFFERS_PER_USER_CYCLE"), 1)
AUTOPOST_SLEEP_SECONDS = _as_int(os.getenv("AUTOPOST_SLEEP_SECONDS"), 60)
REFILL_CHECK_INTERVAL_SECONDS = _as_int(os.getenv("REFILL_CHECK_INTERVAL_SECONDS"), 60)
WATCHLIST_CHECK_INTERVAL_SECONDS = _as_int(os.getenv("WATCHLIST_CHECK_INTERVAL_SECONDS"), 900)

# Refill
ENABLE_INSTANT_POST_AFTER_REFILL = _as_bool(os.getenv("ENABLE_INSTANT_POST_AFTER_REFILL"), False)
REFILL_BATCH_SIZE = _as_int(os.getenv("REFILL_BATCH_SIZE"), 2)
INVALID_BUFFER_QUARANTINE_HOURS = _as_int(os.getenv("INVALID_BUFFER_QUARANTINE_HOURS"), 12)

# Social opzionali
ENABLE_FACEBOOK_POSTING = _as_bool(os.getenv("ENABLE_FACEBOOK_POSTING"), True)
ENABLE_INSTAGRAM_POSTING = _as_bool(os.getenv("ENABLE_INSTAGRAM_POSTING"), True)
