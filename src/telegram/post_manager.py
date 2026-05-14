import logging
from typing import Iterable

import requests

from src.configs.settings import TELEGRAM_BOT_TOKEN
from src.database.user_data_manager import load_user_data

logger = logging.getLogger(__name__)


def _targets_for_user(user_id: int) -> list:
    data = load_user_data().get(str(user_id), {})
    targets = data.get("telegram_channels") or [user_id]
    if not isinstance(targets, list):
        targets = [targets]
    return targets


def _send_message(chat_id, text: str, parse_mode: str = "Markdown") -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[POST_MANAGER] TELEGRAM_BOT_TOKEN mancante")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        if res.status_code != 200:
            logger.warning(f"[POST_MANAGER] sendMessage fallito chat={chat_id} status={res.status_code} body={res.text}")
            return False
        return True
    except Exception:
        logger.exception(f"[POST_MANAGER] Errore invio messaggio chat={chat_id}")
        return False


def _send_photo(chat_id, text: str, image_url: str, parse_mode: str = "Markdown") -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[POST_MANAGER] TELEGRAM_BOT_TOKEN mancante")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        res = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "photo": image_url,
                "caption": text,
                "parse_mode": parse_mode,
            },
            timeout=30,
        )
        if res.status_code != 200:
            logger.warning(f"[POST_MANAGER] sendPhoto fallito chat={chat_id} status={res.status_code} body={res.text}")
            return False
        return True
    except Exception:
        logger.exception(f"[POST_MANAGER] Errore invio foto chat={chat_id}")
        return False


def send_post_to_channels(user_id: int, text: str, image_url: str | None = None) -> bool:
    """Pubblica un testo/foto sui canali configurati dell'utente senza placeholder hardcoded."""
    ok_any = False
    for target in _targets_for_user(user_id):
        if image_url:
            ok = _send_photo(target, text, image_url)
        else:
            ok = _send_message(target, text)
        ok_any = ok_any or ok
    return ok_any
