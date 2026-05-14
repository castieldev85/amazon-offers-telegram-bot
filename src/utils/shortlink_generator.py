import time
import json
import os
import logging
from datetime import datetime
from urllib.parse import quote_plus

from src.configs.settings import DEFAULT_AFFILIATE_TAG, LINK_MAP_PATH
from src.database.user_data_manager import load_user_data

logger = logging.getLogger(__name__)

DEFAULT_TAG_ID = DEFAULT_AFFILIATE_TAG


def get_affiliate_tag(user_id: int) -> str:
    """
    Restituisce il tag affiliato dell'utente solo se ha una licenza attiva e non scaduta.
    Altrimenti usa il tag default configurato nel file .env.
    """
    data = load_user_data()
    user = data.get(str(user_id), {})

    if not user.get("has_license", False):
        return DEFAULT_TAG_ID

    expires_str = user.get("license_expires")
    if not expires_str:
        return DEFAULT_TAG_ID

    try:
        expires = datetime.strptime(expires_str, "%Y-%m-%d").date()
    except ValueError:
        logger.warning(f"[AFFILIATE] Data licenza non valida per user_id={user_id}: {expires_str}")
        return DEFAULT_TAG_ID

    if datetime.now().date() > expires:
        return DEFAULT_TAG_ID

    tag_id = str(user.get("tag_id", "")).strip()
    return tag_id or DEFAULT_TAG_ID


def _load_link_map() -> dict:
    if not os.path.exists(LINK_MAP_PATH):
        return {}

    try:
        with open(LINK_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except json.JSONDecodeError:
        logger.warning("[AFFILIATE] link_map JSON corrotto, lo ricreo.")
    except Exception as e:
        logger.exception(f"[AFFILIATE] Errore lettura {LINK_MAP_PATH}: {e}")
    return {}


def _save_link_map(mapping: dict) -> None:
    try:
        tmp_path = f"{LINK_MAP_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, LINK_MAP_PATH)
    except Exception as e:
        logger.exception(f"[AFFILIATE] Errore salvataggio {LINK_MAP_PATH}: {e}")


def generate_affiliate_link(user_id: int, asin: str) -> str:
    """
    Costruisce il link affiliato Amazon e registra user+ASIN in link_map.json.
    """
    asin = str(asin or "").strip().upper()
    if not asin:
        logger.warning(f"[AFFILIATE] ASIN vuoto per user_id={user_id}")
        return "https://www.amazon.it/"

    tag = get_affiliate_tag(user_id)
    affiliate_url = f"https://www.amazon.it/dp/{quote_plus(asin)}?tag={quote_plus(tag)}"

    mapping = _load_link_map()
    map_key = f"{user_id}:{asin}"
    mapping[map_key] = {
        "chat_id": user_id,
        "asin": asin,
        "tag": tag,
        "url": affiliate_url,
        "timestamp": int(time.time()),
    }
    _save_link_map(mapping)

    return affiliate_url
