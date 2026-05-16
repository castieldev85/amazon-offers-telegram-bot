
from __future__ import annotations

import asyncio
import html
import logging
import os
import threading
import re
import shutil
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from src.buffer.buffer_manager import add_products_to_buffer
from src.scraper.product_scraper import extract_product_info
from src.utils.product import Product

logger = logging.getLogger(__name__)

TELEGRAM_SOURCE_CATEGORY = "cat_telegram_sources"

AMAZON_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:amazon\.[a-z.]+|amzn\.to|amzn\.eu|a\.co)/[^\s\)\]\}\"'<>]+",
    re.IGNORECASE,
)
REDIRECT_KEYS = ("url", "u", "target", "to", "redirect", "link", "q", "r")
ASIN_PATTERNS = [
    re.compile(r"/(?:dp|gp/product|product|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?#&]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[?&#])(?:asin|ASIN)=([A-Z0-9]{10})(?:[&#]|$)", re.IGNORECASE),
    re.compile(r"(?:^|\b)(B0[A-Z0-9]{8})(?:\b|$)", re.IGNORECASE),
]
SHORT_AMAZON_HOSTS = {"amzn.to", "www.amzn.to", "amzn.eu", "www.amzn.eu", "a.co", "www.a.co"}

PRICE_RE = re.compile(r"(?<![A-Z0-9])(\d{1,4}(?:[\.\s]\d{3})*(?:[,.]\d{1,2})?|\d{1,4})\s*(?:€|eur|euro)(?![A-Z0-9])", re.IGNORECASE)
PERCENT_RE = re.compile(r"(?:-|−)?\s*(\d{1,2}(?:[,.]\d{1,2})?)\s*%")
COUPON_RE = re.compile(r"(?:coupon|codice|promo|sconto)\s*(?:da|di|extra|:)?\s*(\d{1,3}(?:[,.]\d{1,2})?\s*%|\d{1,4}(?:[,.]\d{1,2})?\s*€)", re.IGNORECASE)

@dataclass
class TelegramImportResult:
    channel: str
    found_links: int = 0
    found_asins: int = 0
    added_products: int = 0
    skipped_invalid: int = 0
    errors: int = 0
    message: str = ""

@dataclass
class TelegramOfferSignal:
    source: str
    asin: str
    url: str = ""
    text: str = ""
    title_hint: str = ""
    price_hint: float = 0.0
    old_price_hint: float = 0.0
    discount_hint: float = 0.0
    coupon_text: str = ""
    image_hint: str = ""


def normalize_channel_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for prefix in ("https://t.me/s/", "http://t.me/s/", "https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/", "t.me/s/", "t.me/", "telegram.me/"):
        raw = raw.replace(prefix, "")
    raw = raw.split("?")[0].split("/")[0].strip()
    if raw.startswith("@"):
        raw = raw[1:]
    raw = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if not raw or len(raw) < 5:
        return ""
    return "@" + raw


def _public_preview_url(channel: str) -> str:
    return f"https://t.me/s/{normalize_channel_name(channel).lstrip('@')}"


def _clean_url(url: str) -> str:
    url = html.unescape((url or "").strip())
    url = unquote(url)
    url = url.rstrip(".,;:!?)\"]}'")
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith(("www.amazon", "amazon.", "amzn.", "a.co/")):
        url = "https://" + url
    return url


def _decode_text_variants(text: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    def add(value: str) -> None:
        value = html.unescape(str(value or "")).strip()
        if value and value not in seen:
            seen.add(value)
            variants.append(value)
    add(text)
    current = html.unescape(str(text or ""))
    for _ in range(5):
        decoded = unquote(current)
        add(decoded)
        if decoded == current:
            break
        current = decoded
    for value in list(variants):
        try:
            query = parse_qs(urlparse(value).query)
            for key in REDIRECT_KEYS:
                for item in query.get(key, []):
                    add(item)
                    add(unquote(item))
        except Exception:
            continue
    return variants


def _extract_asins_from_text(text: str) -> list[str]:
    asins: list[str] = []
    seen: set[str] = set()
    for variant in _decode_text_variants(text):
        for pattern in ASIN_PATTERNS:
            for match in pattern.finditer(variant):
                asin = (match.group(1) or "").strip().upper()
                if len(asin) == 10 and asin not in seen:
                    seen.add(asin)
                    asins.append(asin)
    return asins


def _parse_float_it(value: str) -> float:
    try:
        v = re.sub(r"[^0-9,.]", "", str(value or ""))
        if not v:
            return 0.0
        if "," in v and "." in v:
            v = v.replace(".", "").replace(",", ".")
        elif "," in v:
            v = v.replace(",", ".")
        parts = v.split(".")
        if len(parts) > 2:
            v = "".join(parts[:-1]) + "." + parts[-1]
        return float(v)
    except Exception:
        return 0.0


def _extract_price_hints(text: str) -> tuple[float, float]:
    prices: list[float] = []
    for m in PRICE_RE.finditer(text or ""):
        value = _parse_float_it(m.group(1))
        if 0.01 <= value <= 10000:
            prices.append(round(value, 2))
    clean: list[float] = []
    for p in prices:
        if p not in clean:
            clean.append(p)
    if not clean:
        return 0.0, 0.0
    current = min(clean)
    old_candidates = [p for p in clean if p > current]
    old = max(old_candidates) if old_candidates else 0.0
    if old and current > 0 and old / current > 4.0:
        old = 0.0
    return current, old


def _extract_discount_hint(text: str, price: float = 0.0, old_price: float = 0.0) -> float:
    if old_price > price > 0:
        return round(((old_price - price) / old_price) * 100, 1)
    candidates = []
    for m in PERCENT_RE.finditer(text or ""):
        value = _parse_float_it(m.group(1))
        if 1 <= value <= 90:
            candidates.append(value)
    return round(max(candidates), 1) if candidates else 0.0


def _extract_coupon_hint(text: str) -> str:
    for m in COUPON_RE.finditer(html.unescape(text or "")):
        value = re.sub(r"\s+", " ", m.group(1)).strip()
        if value:
            return f"Coupon {value}"
    return ""



def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "si", "sì"}


def _clean_telegram_source_image(img):
    """Pulisce le immagini importate dai canali Telegram.

    Molti canali offerte non pubblicano la foto prodotto originale, ma una grafica già
    brandizzata con prezzo, badge, watermark o @canale sorgente. Se la usiamo così,
    nella nostra immagine finale finisce anche il brand del canale sorgente.

    La pulizia è conservativa:
    - sulle immagini larghe/composite taglia la zona sinistra, dove di solito c'è il prodotto;
    - rimuove così il pannello prezzo/brand a destra;
    - crea un'immagine quadrata con sfondo bianco, adatta al nostro image_builder.
    """
    try:
        from PIL import Image

        if not _env_bool("TELEGRAM_SOURCE_CLEAN_MEDIA", True):
            return img.convert("RGB")

        img = img.convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return img

        ratio = w / float(h)

        # Caso tipico delle fonti offerte: grafica 16:9 / 900x500 con prodotto a sinistra
        # e prezzo/watermark/canale a destra. Tagliamo solo la parte prodotto.
        if ratio >= 1.30:
            crop_w = min(w, max(int(h * 1.05), int(w * 0.48)))
            crop_w = min(crop_w, int(w * 0.62))
            img = img.crop((0, 0, crop_w, h))
            w, h = img.size

        # Se resta una striscia bassa molto probabile di watermark, togliamo solo una piccola
        # porzione. Non lo facciamo sulle immagini troppo piccole per evitare tagli aggressivi.
        if _env_bool("TELEGRAM_SOURCE_TRIM_BOTTOM_WATERMARK", True) and h >= 350:
            img = img.crop((0, 0, w, int(h * 0.96)))
            w, h = img.size

        # Output quadrato pulito su sfondo bianco. Non upscaliamo troppo: il builder finale
        # applicherà poi il proprio layout.
        canvas_size = max(w, h, 900)
        canvas = Image.new("RGB", (canvas_size, canvas_size), "white")
        scale = min(canvas_size * 0.88 / max(w, 1), canvas_size * 0.88 / max(h, 1), 1.8)
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        canvas.paste(resized, ((canvas_size - new_w) // 2, (canvas_size - new_h) // 2))
        return canvas
    except Exception:
        logger.debug("[TG-SOURCES] Pulizia immagine Telegram non riuscita", exc_info=True)
        return img.convert("RGB")


def _telegram_media_store_dir() -> str:
    """Cartella persistente per le foto importate da Telegram.

    Non usiamo una cartella temporanea per le immagini sorgente perché il prodotto
    resta nel buffer e può essere pubblicato diversi minuti/ore dopo l'import.
    """
    path = os.getenv("TELEGRAM_SOURCE_MEDIA_PATH", "telegram_source_media").strip() or "telegram_source_media"
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)


def _persist_local_image(local_path: str, asin: str = "") -> str:
    """Copia/converte una foto Telethon in una posizione stabile e ritorna un path assoluto.

    Telethon salva spesso le immagini in cartelle temporanee/relative. Per evitare
    pubblicazioni senza foto, salviamo una copia persistente collegata all'ASIN.
    """
    try:
        if not local_path or not os.path.exists(str(local_path)):
            return ""

        from PIL import Image

        safe_asin = re.sub(r"[^A-Za-z0-9_-]", "", str(asin or "telegram")) or "telegram"
        output = os.path.join(_telegram_media_store_dir(), f"{safe_asin}_{int(time.time())}.jpg")

        try:
            img = Image.open(local_path).convert("RGB")
            img = _clean_telegram_source_image(img)
            img.thumbnail((1400, 1400))
            img.save(output, format="JPEG", quality=92, optimize=True)
            return os.path.abspath(output)
        except Exception:
            # Se PIL non riesce, copiamo comunque il file originale.
            ext = os.path.splitext(str(local_path))[1] or ".jpg"
            output = os.path.join(_telegram_media_store_dir(), f"{safe_asin}_{int(time.time())}{ext}")
            shutil.copy2(local_path, output)
            return os.path.abspath(output)
    except Exception:
        logger.debug("[TG-SOURCES] Impossibile rendere persistente media Telegram", exc_info=True)
        return ""


def _image_value_is_usable(value) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "nan", "n/d", "false"}:
        return False
    if text.lower().startswith(("http://", "https://")):
        return True
    return os.path.exists(text)




def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw.replace(",", ".")) if raw else float(default)
    except Exception:
        return float(default)


def _product_price_value(product: Product | None) -> float:
    if not product:
        return 0.0
    try:
        return float(str(getattr(product, "price", 0) or 0).replace(",", "."))
    except Exception:
        return 0.0


def _price_diff_percent(a: float, b: float) -> float:
    try:
        a = float(a or 0)
        b = float(b or 0)
        if a <= 0 or b <= 0:
            return 0.0
        return abs(a - b) / max(a, b) * 100.0
    except Exception:
        return 0.0


def _apply_safe_old_price(product: Product, old_price_hint: float) -> None:
    """Applica un vecchio prezzo solo se coerente con il prezzo reale attuale."""
    current = _product_price_value(product)
    try:
        old = float(old_price_hint or 0)
    except Exception:
        old = 0.0
    if current <= 0 or old <= current:
        return
    ratio = old / current if current else 999.0
    discount = round(((old - current) / old) * 100.0, 1)
    if ratio <= 4.0 and 5 <= discount <= 70:
        product.old_price = round(old, 2)
        product.discount = discount

def _clean_image_url(value: str) -> str:
    url = html.unescape(str(value or "")).strip().strip('"\' ')
    url = unquote(url)
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        return ""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    # Telegram sometimes exposes tiny emoji/sticker assets: keep only plausible product/media images.
    low = url.lower()
    if any(x in low for x in ("emoji", "avatar", "userpic")):
        return ""
    return url


def _extract_image_hint_from_message(msg) -> str:
    """Estrae l'immagine del post dalla preview pubblica Telegram.

    Molti canali pubblicano già la foto prodotto nel messaggio; quando Amazon/PA-API
    non restituisce immagine, questa immagine è il fallback migliore.
    """
    candidates: list[str] = []

    # t.me/s usa spesso background-image:url('...') sulla photo_wrap.
    for node in msg.select('[style*="background-image"], .tgme_widget_message_photo_wrap'):
        style = node.get("style", "") or ""
        for m in re.finditer(r"url\((['\"]?)(.*?)\1\)", style, flags=re.IGNORECASE):
            candidates.append(m.group(2))
        href = node.get("href")
        if href:
            candidates.append(href)

    # Alcune preview usano img src / data-src.
    for node in msg.select("img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']"):
        for attr in ("src", "data-src", "content"):
            value = node.get(attr)
            if value:
                candidates.append(value)

    # Fallback: cerca URL immagini nel blocco HTML del messaggio.
    raw = str(msg)
    candidates.extend(re.findall(r"https?://[^\s'\"<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s'\"<>]*)?", raw, flags=re.IGNORECASE))

    for value in candidates:
        url = _clean_image_url(value)
        if url:
            return url
    return ""


def _clean_title_hint(text: str) -> str:
    lines = re.split(r"[\r\n]+|\s{2,}", html.unescape(text or ""))
    cleaned: list[str] = []
    stop_words = r"\b(?:prezzo|prima|invece|coupon|codice|promo|offerta|amazon|sconto|risparmi|risparmio|link|acquista|compra|solo|oggi)\b\s*:?)"
    for line in lines:
        line = re.sub(r"https?://\S+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"(?:www\.)?(?:amazon\.[a-z.]+|amzn\.to|amzn\.eu|a\.co)/\S+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\bB0[A-Z0-9]{8}\b", "", line, flags=re.IGNORECASE)
        line = re.sub(r"[#@][\w_]+", "", line)
        line = re.sub(r"[🔥🚨✅⭐️💥🛒👉👇📌🎯💶📉🏷️🎟️🚀]+", " ", line)
        line = re.sub(r"\d{1,4}(?:[\.,]\d{1,2})?\s*(?:€|eur|euro)", "", line, flags=re.IGNORECASE)
        line = re.sub(r"(?:-|−)?\s*\d{1,2}(?:[,.]\d{1,2})?\s*%", "", line)
        line = re.sub(r"\b(?:prezzo|prima|invece|coupon|codice|promo|offerta|amazon|sconto|risparmi|risparmio|link|acquista|compra|solo|oggi)\b\s*:?", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line).strip(" -–|•:;,.…")
        if 12 <= len(line) <= 180:
            cleaned.append(line)
    if cleaned:
        return cleaned[0][:160].strip()
    return ""


# Compatibilità interna usata dal ramo Telethon.
# Nelle versioni precedenti alcune chiamate puntavano a questi nomi privati:
# li lasciamo come wrapper espliciti per evitare fallback inutili alla preview pubblica.
def _extract_title_hint(text: str) -> str:
    return _clean_title_hint(text)


def _extract_amazon_links_from_text(text: str) -> list[str]:
    return extract_amazon_links_from_text(text)


def extract_asin_from_url_like(value: str) -> str | None:
    return _asin_from_link_or_text(value)


def _resolve_short_amazon_link(url: str, timeout: int = 12) -> str:
    url = _clean_url(url)
    if (urlparse(url).netloc or "").lower() not in SHORT_AMAZON_HOSTS:
        return url
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36", "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}
    for method in (requests.head, requests.get):
        try:
            resp = method(url, allow_redirects=True, timeout=timeout, headers=headers)
            if resp.url and resp.url != url:
                return resp.url
        except Exception:
            continue
    logger.info("[TG-SOURCES] Short link non risolto: %s", url)
    return url


def extract_amazon_links_from_text(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for variant in _decode_text_variants(text):
        for match in AMAZON_LINK_RE.findall(variant):
            link = _clean_url(match)
            if link and link not in seen:
                seen.add(link)
                links.append(link)
        try:
            query = parse_qs(urlparse(variant).query)
            for key in REDIRECT_KEYS:
                for item in query.get(key, []):
                    for sub_link in AMAZON_LINK_RE.findall(unquote(html.unescape(item))):
                        link = _clean_url(sub_link)
                        if link and link not in seen:
                            seen.add(link)
                            links.append(link)
        except Exception:
            pass
    return links


def _asin_from_link_or_text(value: str) -> str | None:
    asins = _extract_asins_from_text(value or "")
    if asins:
        return asins[0]
    resolved = _resolve_short_amazon_link(value or "")
    asins = _extract_asins_from_text(resolved)
    return asins[0] if asins else None


def _signal_from_message(channel: str, msg) -> list[TelegramOfferSignal]:
    chunks = [msg.get_text("\n", strip=True), str(msg)]
    for a in msg.select("a[href]"):
        chunks.append(a.get("href", ""))
        chunks.append(a.get_text(" ", strip=True))
    for attr in ("data-url", "data-href", "onclick"):
        value = msg.get(attr)
        if value:
            chunks.append(str(value))
    blob = "\n".join(chunks)

    links = extract_amazon_links_from_text(blob)
    asins = _extract_asins_from_text(blob)
    signals: list[TelegramOfferSignal] = []
    seen: set[str] = set()

    price, old_price = _extract_price_hints(blob)
    discount = _extract_discount_hint(blob, price, old_price)
    coupon = _extract_coupon_hint(blob)
    image_hint = _extract_image_hint_from_message(msg)
    title = _clean_title_hint(msg.get_text("\n", strip=True)) or _clean_title_hint(blob)

    for link in links:
        asin = _asin_from_link_or_text(link)
        if asin and asin not in seen:
            seen.add(asin)
            signals.append(TelegramOfferSignal(channel, asin, link, blob, title, price, old_price, discount, coupon, image_hint))
    for asin in asins:
        if asin not in seen:
            seen.add(asin)
            signals.append(TelegramOfferSignal(channel, asin, f"https://www.amazon.it/dp/{asin}", blob, title, price, old_price, discount, coupon, image_hint))
    return signals


def fetch_public_channel_offer_signals(channel: str, message_limit: int = 30) -> list[TelegramOfferSignal]:
    channel = normalize_channel_name(channel)
    if not channel:
        raise ValueError("Nome canale Telegram non valido")
    url = _public_preview_url(channel)
    logger.info("[TG-SOURCES] Scansione preview pubblica: %s", url)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36", "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}
    resp = requests.get(url, timeout=25, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.select(".tgme_widget_message")
    if not messages:
        page_text = soup.get_text(" ", strip=True).lower()
        if channel.lower().endswith("bot") or "send message" in page_text or "open in telegram" in page_text:
            logger.warning("[TG-SOURCES] %s non sembra un canale pubblico con preview. Probabile bot o canale privato.", channel)
        else:
            logger.warning("[TG-SOURCES] %s: nessun messaggio pubblico trovato su %s", channel, url)
        return []
    if message_limit > 0:
        messages = messages[-message_limit:]

    signals: list[TelegramOfferSignal] = []
    seen: set[str] = set()
    for msg in messages:
        for signal in _signal_from_message(channel, msg):
            if signal.asin not in seen:
                seen.add(signal.asin)
                signals.append(signal)
    logger.info("[TG-SOURCES] %s: trovati %s link/ASIN candidati", channel, len(signals))
    return signals


def fetch_public_channel_amazon_links(channel: str, message_limit: int = 30) -> list[str]:
    # Compatibilità con eventuali vecchie chiamate: restituisce gli URL dei segnali.
    return [s.url or f"https://www.amazon.it/dp/{s.asin}" for s in fetch_public_channel_offer_signals(channel, message_limit)]


def _merge_source_hints(product: Product | None, signal: TelegramOfferSignal) -> Product | None:
    """Unisce dati Amazon e dati della fonte Telegram senza pubblicare prezzi non verificati.

    Regola V3.26:
    - se Amazon/HTML/Selenium restituisce un prezzo live valido, quello è sempre il prezzo ufficiale;
    - se il prezzo del post Telegram è diverso dal prezzo live oltre la tolleranza, viene ignorato;
    - se Amazon non restituisce nessun prezzo, il prezzo Telegram viene usato solo se
      TELEGRAM_SOURCE_ALLOW_UNVERIFIED_PRICE=true. Di default viene scartato.

    Questo evita casi come fonte Telegram a 15,00€ ma pagina Amazon a 34,68€.
    """
    max_diff = _env_float("TELEGRAM_SOURCE_PRICE_MAX_DIFF_PERCENT", 12.0)
    allow_unverified = _env_bool("TELEGRAM_SOURCE_ALLOW_UNVERIFIED_PRICE", False)

    if product:
        current_price = _product_price_value(product)
        if current_price > 0:
            source_price = float(signal.price_hint or 0)
            mismatch = _price_diff_percent(current_price, source_price) if source_price > 0 else 0.0
            source_price_trusted = not source_price or mismatch <= max_diff

            if source_price and not source_price_trusted:
                logger.warning(
                    "[TG-SOURCES] ASIN %s: prezzo fonte Telegram diverso dal live Amazon. "
                    "uso prezzo Amazon | source=%s live=%s diff=%.1f%%",
                    signal.asin, source_price, current_price, mismatch
                )

            # Coupon/testo fonte solo se il prezzo fonte non è in contrasto con Amazon.
            if source_price_trusted and not getattr(product, "coupon_text", None) and signal.coupon_text:
                product.has_coupon = True
                product.coupon_text = signal.coupon_text

            if (not _image_value_is_usable(getattr(product, "image", None))) and signal.image_hint:
                product.image = signal.image_hint
            elif _image_value_is_usable(getattr(product, "image", None)):
                product.image = str(getattr(product, "image", "")).strip()

            if (not getattr(product, "title", None) or getattr(product, "title") == "Prodotto Amazon") and signal.title_hint:
                product.title = signal.title_hint

            # Vecchio prezzo fonte consentito solo se coerente con il prezzo live.
            if source_price_trusted or not getattr(product, "old_price", None):
                _apply_safe_old_price(product, float(signal.old_price_hint or 0))

            return product

    if signal.price_hint <= 0:
        return None

    if not allow_unverified:
        logger.warning(
            "[TG-SOURCES] Skip %s: prezzo Amazon non verificato. "
            "Prezzo fonte=%s ignorato per evitare mismatch. "
            "Per consentirlo imposta TELEGRAM_SOURCE_ALLOW_UNVERIFIED_PRICE=true",
            signal.asin, signal.price_hint
        )
        return None

    old_price = None
    if signal.old_price_hint > signal.price_hint and signal.old_price_hint / signal.price_hint <= 4.0:
        old_price = round(signal.old_price_hint, 2)

    discount = signal.discount_hint
    if old_price and old_price > signal.price_hint:
        discount = round(((old_price - signal.price_hint) / old_price) * 100, 1)

    title = signal.title_hint or f"Prodotto Amazon {signal.asin}"
    logger.warning(
        "[TG-SOURCES] ASIN %s: uso prezzo NON verificato dal post Telegram | price=%s old=%s discount=%s title='%s'",
        signal.asin, signal.price_hint, old_price, discount, title[:80]
    )
    return Product(
        asin=signal.asin,
        title=title,
        price=round(signal.price_hint, 2),
        old_price=old_price,
        discount=round(discount, 1) if discount else 0,
        image=signal.image_hint if _image_value_is_usable(signal.image_hint) else None,
        category="Offerte Telegram",
        has_coupon=bool(signal.coupon_text),
        coupon_text=signal.coupon_text or None,
        link=signal.url or f"https://www.amazon.it/dp/{signal.asin}",
        promo_code=None,
        is_limited_offer=False,
    )


# ==========================
# TELETHON / ACCOUNT UTENTE
# ==========================

def _telethon_enabled() -> bool:
    return os.getenv("TELETHON_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _telethon_session_path() -> str:
    return os.getenv("TELETHON_SESSION", "telegram_user.session").strip() or "telegram_user.session"


def _telethon_credentials() -> tuple[int, str] | tuple[None, None]:
    api_id_raw = os.getenv("TELETHON_API_ID", "").strip()
    api_hash = os.getenv("TELETHON_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        return None, None
    try:
        return int(api_id_raw), api_hash
    except Exception:
        return None, None


def _run_coro_sync(coro):
    """Esegue una coroutine anche se siamo già dentro un event loop.

    Il bot usa già asyncio; alcune chiamate di refill però arrivano da funzioni sincrone.
    Per evitare `asyncio.run() cannot be called from a running event loop`, in quel caso
    spostiamo Telethon in un thread dedicato.
    """
    try:
        asyncio.get_running_loop()
        has_loop = True
    except RuntimeError:
        has_loop = False

    if not has_loop:
        return asyncio.run(coro)

    box: dict[str, object] = {}

    def runner():
        try:
            box["result"] = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover
            box["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _entity_urls_from_telethon_message(message) -> list[str]:
    urls: list[str] = []
    text = getattr(message, "message", "") or ""
    entities = getattr(message, "entities", None) or []
    for ent in entities:
        # MessageEntityTextUrl ha .url
        url = getattr(ent, "url", None)
        if url:
            urls.append(str(url))
            continue
        # MessageEntityUrl: l'URL è nel testo alla posizione offset/length.
        offset = getattr(ent, "offset", None)
        length = getattr(ent, "length", None)
        if offset is not None and length is not None:
            try:
                piece = text[int(offset): int(offset) + int(length)]
                if piece:
                    urls.append(piece)
            except Exception:
                pass
    return urls


def _button_urls_from_telethon_message(message) -> list[str]:
    urls: list[str] = []
    buttons = getattr(message, "buttons", None) or []
    for row in buttons:
        items = row if isinstance(row, (list, tuple)) else [row]
        for btn in items:
            url = getattr(btn, "url", None)
            if url:
                urls.append(str(url))
    return urls


async def _fetch_telethon_channel_offer_signals_async(channel: str, message_limit: int = 30) -> list[TelegramOfferSignal]:
    from telethon import TelegramClient

    api_id, api_hash = _telethon_credentials()
    if not api_id or not api_hash:
        raise RuntimeError("Telethon non configurato: imposta TELETHON_API_ID e TELETHON_API_HASH nel .env")

    session = _telethon_session_path()
    entity_name = normalize_channel_name(channel)
    if not entity_name:
        raise ValueError("Nome canale/bot Telegram non valido")

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Sessione Telethon non autorizzata. Esegui prima: python telethon_login.py"
            )

        signals: list[TelegramOfferSignal] = []
        seen: set[str] = set()
        logger.info("[TG-SOURCES][TELETHON] Scansione %s limit=%s", entity_name, message_limit)

        max_media_downloads = int(os.getenv("TELETHON_MAX_MEDIA_DOWNLOADS", "5") or "5")
        media_downloads = 0

        async for msg in client.iter_messages(entity_name, limit=max(1, int(message_limit or 30))):
            text = getattr(msg, "message", "") or ""
            chunks = [text]
            chunks.extend(_entity_urls_from_telethon_message(msg))
            chunks.extend(_button_urls_from_telethon_message(msg))
            blob = "\n".join(x for x in chunks if x)
            if not blob.strip():
                continue

            # Prima cerchiamo link/ASIN. Scaricare foto per tutti i messaggi è lento e
            # produce decine di log "Starting direct file download" anche quando il post
            # non contiene nessuna offerta Amazon.
            candidates: list[tuple[str, str]] = []
            for raw in chunks:
                for link in _extract_amazon_links_from_text(raw):
                    asin = extract_asin_from_url_like(link)
                    if asin:
                        candidates.append((asin, link))
                for asin in _extract_asins_from_text(raw):
                    candidates.append((asin, f"https://www.amazon.it/dp/{asin}"))

            if not candidates:
                continue

            # Foto/media: scarichiamo solo i media dei messaggi che contengono davvero
            # un link/ASIN Amazon e solo fino al limite configurato.
            # Nota: molti canali non inviano una "photo" Telegram classica, ma una
            # media preview/webpage. Per questo non controlliamo solo msg.photo: proviamo
            # a scaricare il media del messaggio completo e, se serve, il media interno.
            image_hint = ""
            try:
                has_any_media = bool(getattr(msg, "media", None) or getattr(msg, "photo", None) or getattr(msg, "web_preview", None))
                if has_any_media and media_downloads < max_media_downloads:
                    media_dir = os.path.join("temp", "telegram_sources")
                    os.makedirs(media_dir, exist_ok=True)
                    local_path = None

                    # 1) Messaggio intero: funziona per photo/document e molte preview.
                    try:
                        local_path = await client.download_media(msg, file=media_dir)
                    except Exception:
                        local_path = None

                    # 2) Fallback su msg.media, utile per alcune preview/link card.
                    if not local_path and getattr(msg, "media", None):
                        try:
                            local_path = await client.download_media(getattr(msg, "media"), file=media_dir)
                        except Exception:
                            local_path = None

                    # 3) Fallback su web_preview/photo quando disponibile.
                    if not local_path:
                        web_preview = getattr(msg, "web_preview", None)
                        web_photo = getattr(web_preview, "photo", None) if web_preview else None
                        if web_photo:
                            try:
                                local_path = await client.download_media(web_photo, file=media_dir)
                            except Exception:
                                local_path = None

                    if local_path:
                        media_downloads += 1
                        first_asin = candidates[0][0] if candidates else "telegram"
                        image_hint = _persist_local_image(str(local_path), first_asin)
                        logger.info(
                            "[TG-SOURCES][TELETHON] Foto salvata per %s asin=%s image=%s path=%s",
                            entity_name, first_asin, "yes" if image_hint else "no", image_hint or "N/D"
                        )
                    else:
                        logger.info(
                            "[TG-SOURCES][TELETHON] Media presente ma non scaricabile per %s asin=%s",
                            entity_name, candidates[0][0] if candidates else "N/D"
                        )
            except Exception:
                logger.debug("[TG-SOURCES][TELETHON] Impossibile scaricare media per %s", entity_name, exc_info=True)

            price, old_price = _extract_price_hints(blob)
            discount = _extract_discount_hint(blob, price, old_price)
            coupon = _extract_coupon_hint(blob)
            title = _extract_title_hint(blob)

            for asin, link in candidates:
                asin = (asin or "").strip().upper()
                if len(asin) != 10 or asin in seen:
                    continue
                seen.add(asin)
                signals.append(
                    TelegramOfferSignal(
                        source=entity_name,
                        asin=asin,
                        url=link or f"https://www.amazon.it/dp/{asin}",
                        text=blob,
                        title_hint=title,
                        price_hint=price,
                        old_price_hint=old_price,
                        discount_hint=discount,
                        coupon_text=coupon,
                        image_hint=image_hint,
                    )
                )

        logger.info("[TG-SOURCES][TELETHON] %s: trovati %s link/ASIN candidati", entity_name, len(signals))
        return signals
    finally:
        await client.disconnect()


def fetch_telethon_channel_offer_signals(channel: str, message_limit: int = 30) -> list[TelegramOfferSignal]:
    return _run_coro_sync(_fetch_telethon_channel_offer_signals_async(channel, message_limit))


def fetch_channel_offer_signals(channel: str, message_limit: int = 30) -> list[TelegramOfferSignal]:
    """Legge offerte da Telegram.

    Priorità:
    1. Telethon/account utente, se TELETHON_ENABLED=true e la sessione è pronta.
    2. Preview pubblica t.me/s/nomecanale come fallback.
    """
    if _telethon_enabled():
        try:
            signals = fetch_telethon_channel_offer_signals(channel, message_limit)
            if signals:
                return signals
            logger.info("[TG-SOURCES][TELETHON] %s: nessun segnale, provo fallback preview pubblica", channel)
        except Exception as exc:
            logger.warning("[TG-SOURCES][TELETHON] %s non disponibile: %s. Provo preview pubblica.", channel, exc)
    return fetch_public_channel_offer_signals(channel, message_limit)

def import_channel_offers_to_buffer(user_id: int, channel: str, limit: int = 30) -> TelegramImportResult:
    channel = normalize_channel_name(channel)
    result = TelegramImportResult(channel=channel)
    if not channel:
        result.message = "Nome canale non valido"
        return result
    try:
        signals = fetch_channel_offer_signals(channel, message_limit=limit)
    except Exception as exc:
        logger.exception("[TG-SOURCES] Errore lettura canale %s", channel)
        result.errors += 1
        result.message = str(exc)
        return result

    result.found_links = len(signals)
    products = []
    seen_asins: set[str] = set()
    if not signals:
        result.message = "Nessun link Amazon/ASIN trovato. Se la fonte è un bot o canale privato, abilita Telethon e assicurati che l'account utente abbia accesso alla chat."

    for signal in signals:
        try:
            asin = (signal.asin or "").strip().upper()
            if not asin or asin in seen_asins:
                continue
            seen_asins.add(asin)
            result.found_asins += 1

            product = extract_product_info(asin)
            product = _merge_source_hints(product, signal)
            if not product:
                result.skipped_invalid += 1
                continue

            try:
                price = float(str(getattr(product, "price", 0) or 0).replace(",", "."))
            except Exception:
                price = 0.0
            if price <= 0:
                logger.info("[TG-SOURCES] Skip %s: prezzo non valido/zero anche dopo fallback Telegram", asin)
                result.skipped_invalid += 1
                continue

            product.category = "Offerte Telegram"
            logger.info(
                "[TG-SOURCES] ASIN %s pronto per buffer | price=%s image=%s",
                asin,
                getattr(product, "price", None),
                "yes" if _image_value_is_usable(getattr(product, "image", None)) else "no",
            )
            products.append(product)
        except Exception:
            logger.exception("[TG-SOURCES] Errore import ASIN %s", getattr(signal, "asin", "N/D"))
            result.errors += 1

    if products:
        add_products_to_buffer(user_id, TELEGRAM_SOURCE_CATEGORY, products)
        result.added_products = len(products)
    if not result.message:
        result.message = "ok"

    # Salva statistiche per dashboard Fonti Telegram.
    # In questo modo dal pannello admin puoi vedere, per ogni canale,
    # quanti link/ASIN/offerte ha trovato nell'ultima scansione.
    try:
        from src.telegram_sources.source_store import set_source_stats

        set_source_stats(user_id, channel, {
            "found_links": int(result.found_links or 0),
            "found_asins": int(result.found_asins or 0),
            "added_products": int(result.added_products or 0),
            "skipped_invalid": int(result.skipped_invalid or 0),
            "errors": int(result.errors or 0),
            "message": result.message or "ok",
        })
    except Exception:
        logger.debug("[TG-SOURCES] Salvataggio statistiche fonte non riuscito", exc_info=True)

    logger.info(
        "[TG-SOURCES] Risultato %s | links=%s asin=%s aggiunti=%s scartati=%s errori=%s | %s",
        channel, result.found_links, result.found_asins, result.added_products, result.skipped_invalid, result.errors, result.message,
    )
    return result
