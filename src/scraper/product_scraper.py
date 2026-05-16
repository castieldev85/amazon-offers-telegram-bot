import time
import re
import random
import logging
import requests
from bs4 import BeautifulSoup

from src.utils.product import Product
from src.utils.amazon_api_helper import fetch_product_details_from_api
from src.configs.settings import (
    ASIN_DETAIL_ENABLE_SELENIUM_FALLBACK,
    ASIN_DETAIL_SELENIUM_MAX_PER_REFILL,
)

logger = logging.getLogger(__name__)

# Evita che un refill parallelo apra troppi Chrome contemporaneamente solo per recuperare prezzi mancanti.
import threading
_SELENIUM_DETAIL_SEMAPHORE = threading.BoundedSemaphore(max(1, int(ASIN_DETAIL_SELENIUM_MAX_PER_REFILL)))


# ---------------------------------------------------------
# 🔧 Costanti
# ---------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

TITLE_NOISE_PATTERNS = [
    r"i miei ordini fatti di recente",
    r"pagina successiva",
    r"prodotti sponsorizzati",
    r"prodotti sponsorizzati simili",
    r"specifiche del prodotto",
    r"descrizione prodotto",
    r"bestseller di amazon",
    r"posizione nella classifica",
    r"coupons amazon",
    r"modello",
    r"shop now",
]

COUPON_PATTERNS = [
    r"risparmia\s+\d+\s*%\s+con\s+coupon",
    r"risparmia\s+\d+[.,]?\d*\s*€\s+con\s+coupon",
    r"applica\s+il\s+coupon\s+da\s+\d+\s*%",
    r"applica\s+il\s+coupon\s+da\s+\d+[.,]?\d*\s*€",
    r"applica\s+coupon\s+da\s+\d+\s*%",
    r"applica\s+coupon\s+da\s+\d+[.,]?\d*\s*€",
    r"coupon\s+da\s+\d+\s*%",
    r"coupon\s+da\s+\d+[.,]?\d*\s*€",
    r"\d+\s*%\s*di\s*sconto\s*con\s*coupon",
    r"\d+[.,]?\d*\s*€\s*di\s*sconto\s*con\s*coupon",
]


# ---------------------------------------------------------
# 🔧 Utility
# ---------------------------------------------------------
def to_float(value):
    """
    Converte prezzi Amazon in float robusto.
    Esempi:
    - '1,29 €' -> 1.29
    - '1.299,99' -> 1299.99
    - None -> 0.0
    """
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return 0.0

    try:
        v = str(value).strip()
        if not v:
            return 0.0

        v = v.replace("€", "").replace("EUR", "").replace("eur", "").replace("\u00a0", "").strip()
        v = re.sub(r"[^0-9,\.]", "", v)

        if not v:
            return 0.0

        # Caso europeo: 1.299,99 -> 1299.99
        if "," in v and "." in v:
            v = v.replace(".", "").replace(",", ".")
        elif "," in v:
            v = v.replace(",", ".")

        parts = v.split(".")
        if len(parts) > 2:
            v = "".join(parts[:-1]) + "." + parts[-1]

        return float(v) if v else 0.0
    except Exception:
        return 0.0




def _safe_asin(value) -> str:
    """
    Normalizza e valida un ASIN Amazon.

    Accetta anche link o testi sporchi e prova a estrarre il primo ASIN valido.
    Ritorna stringa vuota se non trova un ASIN valido.
    """
    if value is None:
        return ""

    raw = str(value).strip().upper()
    if not raw:
        return ""

    # Se arriva un link Amazon, estraggo l'ASIN da /dp/, /gp/product/ o qualunque token valido.
    patterns = [
        r"/(?:DP|GP/PRODUCT)/([A-Z0-9]{10})(?:[/?#]|$)",
        r"(?:ASIN=|ASIN%3D)([A-Z0-9]{10})",
        r"\b([A-Z0-9]{10})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().upper()
            if re.fullmatch(r"[A-Z0-9]{10}", candidate):
                return candidate

    # Ultimo fallback: tolgo caratteri non alfanumerici e controllo lunghezza.
    cleaned = re.sub(r"[^A-Z0-9]", "", raw)
    if re.fullmatch(r"[A-Z0-9]{10}", cleaned):
        return cleaned

    logger.warning(f"[ASIN-DETAIL] ASIN non valido/scartato: {value}")
    return ""


def normalize_discount(value) -> float:
    try:
        if value is None:
            return 0.0
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except Exception:
        return 0.0


def is_price_valid(value) -> bool:
    val = to_float(value)
    return val > 0.0


def _normalize_spaces(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _contains_title_noise(text: str) -> bool:
    t = _normalize_spaces(text).lower()
    if not t:
        return False

    for pattern in TITLE_NOISE_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def clean_title(text: str) -> str:
    """
    Pulisce titoli sporchi o troppo lunghi.
    """
    if not text:
        return ""

    text = _normalize_spaces(text)

    # Taglia se compaiono sezioni rumorose
    lower_text = text.lower()
    cut_positions = []

    for pattern in TITLE_NOISE_PATTERNS:
        m = re.search(pattern, lower_text, re.IGNORECASE)
        if m:
            cut_positions.append(m.start())

    if cut_positions:
        text = text[:min(cut_positions)].strip(" -–|,;:")

    text = _normalize_spaces(text)

    # Togli parti duplicate o code troppo lunghe
    if len(text) > 180:
        text = text[:180].rsplit(" ", 1)[0].strip(" -–|,;:")

    return text


def _build_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


def _fetch_product_soup(asin: str):
    """
    Ritorna BeautifulSoup della pagina prodotto Amazon.
    Restituisce None in caso di captcha / errore / status non valido.
    """
    try:
        url = f"https://www.amazon.it/dp/{asin}?th=1&psc=1"

        session = requests.Session()
        session.cookies.set("session-id", str(random.randint(100000, 999999999)))

        time.sleep(random.uniform(0.8, 1.8))

        r = session.get(url, headers=_build_headers(), timeout=15)

        if r.status_code != 200:
            logger.warning(f"[HTML] HTTP {r.status_code} per ASIN {asin}")
            return None

        html_lower = r.text.lower()
        if "captcha" in html_lower or "robot check" in html_lower or "not a robot" in html_lower:
            logger.warning(f"[HTML] CAPTCHA rilevato per ASIN {asin}")
            return None

        return BeautifulSoup(r.text, "html.parser")

    except Exception as e:
        logger.exception(f"[HTML] Errore fetch soup per ASIN {asin}: {e}")
        return None


def extract_title_from_html(asin: str) -> str | None:
    """
    Estrae il titolo dal DOM Amazon.
    """
    try:
        soup = _fetch_product_soup(asin)
        if not soup:
            return None

        selectors = [
            "#productTitle",
            "span#productTitle",
            "h1.a-size-large",
            "meta[property='og:title']",
        ]

        for sel in selectors:
            el = soup.select_one(sel)
            if not el:
                continue

            if el.name == "meta":
                title = el.get("content")
            else:
                title = el.get_text(" ", strip=True)

            title = clean_title(title)
            if title and len(title) >= 6:
                return title

        return None

    except Exception as e:
        logger.exception(f"[HTML] Errore estrazione titolo per ASIN {asin}: {e}")
        return None


def _estimate_coupon_discount_percent(coupon_text: str, price: float) -> float:
    """
    Stima la percentuale del coupon dal testo.
    """
    if not coupon_text:
        return 0.0

    coupon_text = _normalize_spaces(coupon_text)

    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", coupon_text, re.IGNORECASE)
    if m:
        try:
            return round(float(m.group(1).replace(",", ".")), 1)
        except Exception:
            return 0.0

    if price > 0:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(€|euro)", coupon_text, re.IGNORECASE)
        if m:
            try:
                euro_value = float(m.group(1).replace(",", "."))
                return round((euro_value / price) * 100.0, 1)
            except Exception:
                return 0.0

    return 0.0


def _is_old_price_credible(price: float, old_price: float, has_coupon: bool, promo_code: str | None, is_limited_offer: bool) -> bool:
    """
    Evita vecchi prezzi palesemente gonfiati.
    """
    if price <= 0 or old_price <= 0:
        return False

    if old_price <= price:
        return False

    ratio = old_price / price

    # Sempre sospetto se assurdo
    if ratio >= 12:
        return False

    # Molto sospetto se enorme e senza segnali forti
    if ratio >= 8 and not (has_coupon or promo_code or is_limited_offer):
        return False

    return True



# ---------------------------------------------------------
# 🧠 HTML FALLBACK AVANZATO ASIN
# ---------------------------------------------------------
def _extract_json_ld_blocks(soup) -> list[dict]:
    blocks: list[dict] = []
    try:
        for tag in soup.select("script[type='application/ld+json']"):
            raw = tag.string or tag.get_text(" ", strip=True)
            if not raw:
                continue
            try:
                data = __import__('json').loads(raw)
            except Exception:
                continue
            if isinstance(data, list):
                blocks.extend([x for x in data if isinstance(x, dict)])
            elif isinstance(data, dict):
                blocks.append(data)
    except Exception:
        pass
    return blocks


def _extract_price_from_json_ld(soup) -> float | None:
    """Legge il prezzo da JSON-LD, quando Amazon lo espone."""
    for block in _extract_json_ld_blocks(soup):
        try:
            offers = block.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                for key in ("price", "lowPrice", "highPrice"):
                    val = to_float(offers.get(key))
                    if val > 0:
                        return val
        except Exception:
            continue
    return None


def _extract_title_from_json_ld(soup) -> str | None:
    for block in _extract_json_ld_blocks(soup):
        try:
            name = clean_title(block.get("name") or "")
            if name and len(name) >= 6:
                return name
        except Exception:
            continue
    return None


def _extract_image_from_json_ld(soup) -> str | None:
    for block in _extract_json_ld_blocks(soup):
        try:
            image = block.get("image")
            if isinstance(image, list) and image:
                image = image[0]
            if image and isinstance(image, str) and image.startswith("http"):
                return image.strip()
        except Exception:
            continue
    return None


def _extract_meta_price(soup) -> float | None:
    selectors = [
        "meta[property='product:price:amount']",
        "meta[name='twitter:data1']",
        "meta[property='og:price:amount']",
        "meta[itemprop='price']",
    ]
    for sel in selectors:
        try:
            tag = soup.select_one(sel)
            if not tag:
                continue
            value = to_float(tag.get("content") or tag.get("value") or "")
            if value > 0:
                return value
        except Exception:
            continue
    return None


def _extract_image_from_html_soup(soup) -> str | None:
    selectors = [
        "#landingImage",
        "#imgBlkFront",
        "#main-image",
        "#imgTagWrapperId img",
        "img.a-dynamic-image",
        "meta[property='og:image']",
    ]
    for sel in selectors:
        try:
            tag = soup.select_one(sel)
            if not tag:
                continue
            if tag.name == "meta":
                src = tag.get("content")
            else:
                src = tag.get("data-old-hires") or tag.get("src") or ""
                if not src:
                    raw_dynamic = tag.get("data-a-dynamic-image") or ""
                    if raw_dynamic:
                        try:
                            import json
                            data = json.loads(raw_dynamic)
                            if isinstance(data, dict) and data:
                                src = max(data.keys(), key=len)
                        except Exception:
                            pass
            if src and str(src).startswith("http"):
                return str(src).strip()
        except Exception:
            continue
    return None


def _page_says_unavailable(soup) -> bool:
    try:
        text = _normalize_spaces(soup.get_text(" ", strip=True)).lower()
        signals = [
            "attualmente non disponibile",
            "non sappiamo se o quando l'articolo sarà di nuovo disponibile",
            "non disponibile",
            "currently unavailable",
        ]
        return any(sig in text for sig in signals)
    except Exception:
        return False


def enrich_product_from_html(asin: str, product: Product | None = None) -> dict:
    """
    Legge una sola volta la pagina HTML e restituisce tutti i dettagli recuperabili.
    Serve per evitare 3 richieste separate HTML per titolo/prezzo/coupon/promo.
    """
    soup = _fetch_product_soup(asin)
    if not soup:
        return {}

    info: dict = {"unavailable": _page_says_unavailable(soup)}

    # Titolo
    for sel in ["#productTitle", "span#productTitle", "h1.a-size-large", "meta[property='og:title']"]:
        try:
            el = soup.select_one(sel)
            if not el:
                continue
            title = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            title = clean_title(title)
            if title and len(title) >= 6:
                info["title"] = title
                break
        except Exception:
            continue
    if not info.get("title"):
        json_title = _extract_title_from_json_ld(soup)
        if json_title:
            info["title"] = json_title

    # Immagine
    image = _extract_image_from_html_soup(soup) or _extract_image_from_json_ld(soup)
    if image:
        info["image"] = image

    # Prezzo attuale
    price_selectors = [
        "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
        "#corePrice_feature_div span.a-price span.a-offscreen",
        "#apex_desktop span.a-price span.a-offscreen",
        "span.apexPriceToPay span.a-offscreen",
        "span.a-price.aok-align-center span.a-offscreen",
        "span#priceblock_ourprice",
        "span#priceblock_dealprice",
        "span#price_inside_buybox",
        "span.a-price span.a-offscreen",
    ]
    price = None
    for sel in price_selectors:
        try:
            for tag in soup.select(sel):
                value = to_float(tag.get_text(" ", strip=True) or tag.get("aria-label") or "")
                if value > 0:
                    price = value
                    break
            if price:
                break
        except Exception:
            continue
    if not price:
        price = _extract_meta_price(soup) or _extract_price_from_json_ld(soup)
    if price and price > 0:
        info["price"] = price

    # Vecchio prezzo / prezzo barrato
    old_price_selectors = [
        "span.a-price.a-text-price span.a-offscreen",
        "span.basisPrice span.a-offscreen",
        "#corePriceDisplay_desktop_feature_div span.a-text-price span.a-offscreen",
        "span.a-list-price",
        "span#listPriceValue",
        "span.priceBlockStrikePriceString",
        ".a-text-strike",
    ]
    old_candidates = []
    for sel in old_price_selectors:
        try:
            for tag in soup.select(sel):
                value = to_float(tag.get_text(" ", strip=True) or tag.get("aria-label") or "")
                if value > 0:
                    old_candidates.append(value)
        except Exception:
            continue
    if old_candidates:
        current = price or 0
        bigger = [v for v in old_candidates if v > current]
        if bigger:
            info["old_price"] = max(bigger)

    # Coupon
    coupon_candidates = []
    for sel in [
        "#couponFeature",
        "#voucherNode_feature_div",
        "div[data-feature-name='couponFeature']",
        "div[data-feature-name='voucherNode_feature_div']",
        "#promoPriceBlockMessage_feature_div",
        "#applicablePromotionList_feature_div",
    ]:
        try:
            for el in soup.select(sel):
                cleaned = clean_coupon_text(el.get_text(" ", strip=True))
                if cleaned:
                    coupon_candidates.append(cleaned)
        except Exception:
            continue
    if not coupon_candidates:
        full_text = _normalize_spaces(soup.get_text(" ", strip=True))
        for pattern in COUPON_PATTERNS:
            for match in re.findall(pattern, full_text, flags=re.IGNORECASE):
                cleaned = clean_coupon_text(match)
                if cleaned:
                    coupon_candidates.append(cleaned)
    if coupon_candidates:
        seen = set()
        clean = []
        for c in coupon_candidates:
            k = c.lower().strip()
            if k and k not in seen:
                seen.add(k)
                clean.append(c)
        if clean:
            info["has_coupon"] = True
            info["coupon_text"] = clean[0]

    # Promo code
    texts = []
    for sel in [
        "#promoPriceBlockMessage_feature_div",
        "#applicablePromotionList_feature_div",
        "#promotions_feature_div",
        "div[data-feature-name='applicablePromotionList']",
    ]:
        try:
            texts.extend([_normalize_spaces(el.get_text(" ", strip=True)) for el in soup.select(sel)])
        except Exception:
            continue
    if not texts:
        texts.append(_normalize_spaces(soup.get_text(" ", strip=True)))
    for txt in texts:
        up = txt.upper()
        for pattern in [r"applica\s+il\s+codice\s+([A-Z0-9]{4,20})", r"usa\s+il\s+codice\s+([A-Z0-9]{4,20})", r"codice\s+([A-Z0-9]{4,20})"]:
            m = re.search(pattern, up, re.IGNORECASE)
            if m:
                info["promo_code"] = m.group(1).strip().upper()
                return info

    return info


def _selenium_detail_fallback(asin: str) -> dict:
    """
    Ultima spiaggia: apre la pagina prodotto con Selenium solo se API/HTML non hanno prezzo.
    Limitata da semaforo per non aprire troppi Chrome in parallelo.
    """
    if not ASIN_DETAIL_ENABLE_SELENIUM_FALLBACK:
        return {}
    try:
        from src.utils.extract_product_info_selenium import extract_product_info_selenium
    except Exception as e:
        logger.warning(f"[SELENIUM-FALLBACK] Non disponibile per ASIN {asin}: {e}")
        return {}

    url = f"https://www.amazon.it/dp/{asin}?th=1&psc=1"
    acquired = _SELENIUM_DETAIL_SEMAPHORE.acquire(timeout=180)
    if not acquired:
        logger.warning(f"[SELENIUM-FALLBACK] Timeout semaforo per ASIN {asin}")
        return {}
    try:
        logger.warning(f"[SELENIUM-FALLBACK] Avvio dettaglio prodotto per ASIN {asin}")
        prod = extract_product_info_selenium(url)
        if not prod:
            return {}
        return {
            "title": clean_title(getattr(prod, "title", "") or ""),
            "price": to_float(getattr(prod, "price", 0)),
            "old_price": to_float(getattr(prod, "old_price", 0)),
            "discount": normalize_discount(getattr(prod, "discount", 0)),
            "image": getattr(prod, "image", None),
            "has_coupon": bool(getattr(prod, "has_coupon", False)),
            "promo_code": getattr(prod, "promo_code", None),
            "is_limited_offer": bool(getattr(prod, "is_limited_offer", False)),
        }
    except Exception as e:
        logger.exception(f"[SELENIUM-FALLBACK] Errore ASIN {asin}: {e}")
        return {}
    finally:
        try:
            _SELENIUM_DETAIL_SEMAPHORE.release()
        except Exception:
            pass

# ---------------------------------------------------------
# 🕷️ HTML FALLBACK PREZZO
# ---------------------------------------------------------
def extract_price_from_html(asin: str):
    """
    Fallback HTML per estrarre prezzo e prezzo barrato.
    Compatibile con layout Amazon recenti e con rilevazione captcha.
    """
    try:
        soup = _fetch_product_soup(asin)
        if not soup:
            return None, None

        # PREZZO ATTUALE
        price_selectors = [
            "span.a-price.aok-align-center span.a-offscreen",
            "span.apexPriceToPay span.a-offscreen",
            "div#corePrice_feature_div span.a-offscreen",
            "span#priceblock_ourprice",
            "span#priceblock_dealprice",
            "span#price_inside_buybox",
            "span.a-price span.a-offscreen",
        ]

        price = None
        for sel in price_selectors:
            tag = soup.select_one(sel)
            if tag:
                value = to_float(tag.get_text())
                if value > 0:
                    price = value
                    break

        # PREZZO BARRATO
        old_price_selectors = [
            "span.a-price.a-text-price span.a-offscreen",
            "span.basisPrice span.a-offscreen",
            "span.a-list-price",
            "span#listPriceValue",
            "span.priceBlockStrikePriceString",
        ]

        old_price_candidates = []
        for sel in old_price_selectors:
            tags = soup.select(sel)
            for tag in tags:
                value = to_float(tag.get_text())
                if value > 0:
                    old_price_candidates.append(value)

        old_price = None
        if old_price_candidates:
            if price and price > 0:
                bigger = [x for x in old_price_candidates if x > price]
                if bigger:
                    old_price = max(bigger)
                else:
                    old_price = max(old_price_candidates)
            else:
                old_price = max(old_price_candidates)

        return price, old_price

    except Exception as e:
        logger.exception(f"[HTML] Errore fallback prezzo per ASIN {asin}: {e}")
        return None, None


# ---------------------------------------------------------
# 🏷️ HTML FALLBACK COUPON
# ---------------------------------------------------------
def clean_coupon_text(text: str) -> str | None:
    """
    Pulisce il testo coupon eliminando rumore, prezzi duplicati e testo irrilevante.
    Restituisce una stringa corta e leggibile oppure None.
    """
    if not text:
        return None

    text = _normalize_spaces(text)

    stop_words = [
        "pagina successiva",
        "prodotti sponsorizzati",
        "specifiche del prodotto",
        "bestseller di amazon",
        "posizione nella classifica",
        "produttore",
        "offerta lampo",
        "modello",
        "coupons amazon",
        "recensioni",
        "descrizione prodotto",
    ]

    lower_text = text.lower()
    cut_positions = [lower_text.find(word) for word in stop_words if word in lower_text]
    cut_positions = [p for p in cut_positions if p >= 0]
    if cut_positions:
        text = text[:min(cut_positions)].strip()

    # Rimuovi prezzi spezzati o duplicati all'inizio
    text = re.sub(r"^[\d\s,\.€]+", "", text).strip()
    text = re.sub(r"(\d+[.,]?\d*\s*€)\s+\1", r"\1", text, flags=re.IGNORECASE)

    for pattern in COUPON_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            result = m.group(0).strip(" -–|,;:.")
            result = _normalize_spaces(result)
            if result:
                return result[0].upper() + result[1:]

    if "coupon" in text.lower():
        text = text[:90].strip(" -–|,;:.")
        text = _normalize_spaces(text)
        if len(text) >= 8:
            return text

    return None


def extract_coupon_from_html(asin: str):
    """
    Estrae info coupon dall'HTML della pagina prodotto.
    Restituisce: (has_coupon, coupon_text)
    """
    try:
        soup = _fetch_product_soup(asin)
        if not soup:
            return False, None

        coupon_candidates = []

        selectors = [
            "#couponFeature",
            "#voucherNode_feature_div",
            "div[data-feature-name='couponFeature']",
            "div[data-feature-name='voucherNode_feature_div']",
            "#promoPriceBlockMessage_feature_div",
            "#applicablePromotionList_feature_div",
        ]

        for sel in selectors:
            try:
                elements = soup.select(sel)
                for el in elements:
                    txt = el.get_text(" ", strip=True)
                    cleaned = clean_coupon_text(txt)
                    if cleaned:
                        coupon_candidates.append(cleaned)
            except Exception:
                continue

        # fallback sul testo completo
        if not coupon_candidates:
            full_text = soup.get_text(" ", strip=True)
            full_text = _normalize_spaces(full_text)

            for pattern in COUPON_PATTERNS:
                matches = re.findall(pattern, full_text, flags=re.IGNORECASE)
                for match in matches:
                    cleaned = clean_coupon_text(match)
                    if cleaned:
                        coupon_candidates.append(cleaned)

        # deduplica
        final_candidates = []
        seen = set()
        for c in coupon_candidates:
            key = c.lower().strip()
            if key and key not in seen:
                seen.add(key)
                final_candidates.append(c)

        if final_candidates:
            best = final_candidates[0]
            logger.info(f"[COUPON] ASIN {asin} -> {best}")
            return True, best

        return False, None

    except Exception as e:
        logger.exception(f"[HTML] Errore estrazione coupon per ASIN {asin}: {e}")
        return False, None


# ---------------------------------------------------------
# 🔐 HTML FALLBACK PROMO CODE
# ---------------------------------------------------------
def extract_promo_code_from_html(asin: str):
    """
    Prova a leggere un eventuale codice promo dalla pagina.
    """
    try:
        soup = _fetch_product_soup(asin)
        if not soup:
            return None

        selectors = [
            "#promoPriceBlockMessage_feature_div",
            "#applicablePromotionList_feature_div",
            "#promotions_feature_div",
            "div[data-feature-name='applicablePromotionList']",
        ]

        texts = []

        for sel in selectors:
            try:
                for el in soup.select(sel):
                    txt = _normalize_spaces(el.get_text(" ", strip=True))
                    if txt:
                        texts.append(txt)
            except Exception:
                continue

        if not texts:
            full_text = _normalize_spaces(soup.get_text(" ", strip=True))
            texts.append(full_text)

        patterns = [
            r"applica\s+il\s+codice\s+([A-Z0-9]{4,20})",
            r"usa\s+il\s+codice\s+([A-Z0-9]{4,20})",
            r"codice\s+([A-Z0-9]{4,20})",
        ]

        for txt in texts:
            up = txt.upper()
            for pattern in patterns:
                m = re.search(pattern, up, re.IGNORECASE)
                if m:
                    code = m.group(1).strip().upper()
                    if code and len(code) >= 4:
                        logger.info(f"[PROMO] ASIN {asin} -> codice {code}")
                        return code

        return None

    except Exception as e:
        logger.exception(f"[HTML] Errore estrazione promo code per ASIN {asin}: {e}")
        return None


# ---------------------------------------------------------
# 🎯 FUNZIONE PRINCIPALE
# ---------------------------------------------------------
def extract_product_info(asin: str) -> Product | None:
    """
    Estrae i dati di un prodotto usando pipeline robusta:
    1) Amazon PA-API
    2) HTML Amazon in una sola richiesta
    3) Selenium solo se il prezzo resta mancante/0

    Obiettivo: evitare prodotti a prezzo 0 nel buffer e arricchire meglio ASIN, titolo, immagine, coupon e prezzo barrato.
    """
    try:
        asin = _safe_asin(asin)
        if not asin:
            return None

        product = fetch_product_details_from_api(asin)

        # Se PA-API non risponde, creo comunque una base vuota e provo HTML/Selenium.
        if not product:
            logger.warning(f"[ASIN-DETAIL] PA-API senza risultato → provo HTML/Selenium per ASIN {asin}")
            product = Product(
                asin=asin,
                title="Prodotto Amazon",
                price=0,
                old_price=None,
                discount=0,
                image=None,
                link=f"https://www.amazon.it/dp/{asin}",
                category="Offerta",
            )

        title = clean_title(getattr(product, "title", None) or "Prodotto Amazon")
        image = getattr(product, "image", None)
        link = getattr(product, "link", None)
        category = getattr(product, "category", "Offerta") or "Offerta"
        is_limited_offer = bool(getattr(product, "is_limited_offer", False))
        promo_code = getattr(product, "promo_code", None)

        price = to_float(getattr(product, "price", 0))
        old_price = to_float(getattr(product, "old_price", 0))
        discount = normalize_discount(getattr(product, "discount", 0))

        has_coupon = bool(getattr(product, "has_coupon", False))
        coupon_text = clean_coupon_text(getattr(product, "coupon_text", None))

        # Recalcolo discount se manca ma old_price sembra valido
        if discount <= 0 and old_price > price > 0:
            discount = round(((old_price - price) / old_price) * 100, 1)

        # Un solo passaggio HTML per recuperare tutti i dettagli mancanti/sporchi.
        needs_html = (
            price <= 0
            or not image
            or not title
            or _contains_title_noise(title)
            or len(title) < 8
            or not coupon_text
            or not promo_code
        )
        html_info = {}
        if needs_html:
            html_info = enrich_product_from_html(asin, product)

            if html_info.get("unavailable") and price <= 0:
                logger.info(f"[ASIN-DETAIL] ASIN {asin} non disponibile e senza prezzo → scarto")
                return None

            if html_info.get("title") and (_contains_title_noise(title) or len(title) < 8 or title == "Prodotto Amazon"):
                title = html_info["title"]

            if html_info.get("image") and not image:
                image = html_info["image"]

            if html_info.get("price") and to_float(html_info["price"]) > 0:
                price = to_float(html_info["price"])

            if html_info.get("old_price") and to_float(html_info["old_price"]) > price:
                old_price = to_float(html_info["old_price"])

            if html_info.get("has_coupon"):
                has_coupon = True
                coupon_text = clean_coupon_text(html_info.get("coupon_text")) or html_info.get("coupon_text")

            if html_info.get("promo_code") and not promo_code:
                promo_code = html_info["promo_code"]

        # Ultima spiaggia: Selenium se il prezzo resta 0/N.D.
        if price <= 0:
            selenium_info = _selenium_detail_fallback(asin)
            if selenium_info:
                if selenium_info.get("price") and to_float(selenium_info["price"]) > 0:
                    price = to_float(selenium_info["price"])
                if selenium_info.get("old_price") and to_float(selenium_info["old_price"]) > price:
                    old_price = to_float(selenium_info["old_price"])
                if selenium_info.get("discount"):
                    discount = normalize_discount(selenium_info.get("discount"))
                if selenium_info.get("title") and (title == "Prodotto Amazon" or len(title) < 8):
                    title = selenium_info["title"]
                if selenium_info.get("image") and not image:
                    image = selenium_info["image"]
                if selenium_info.get("has_coupon"):
                    has_coupon = True
                if selenium_info.get("promo_code") and not promo_code:
                    promo_code = selenium_info["promo_code"]
                if selenium_info.get("is_limited_offer"):
                    is_limited_offer = True

        if price <= 0:
            logger.warning(f"[ASIN-DETAIL] Scarto ASIN {asin}: prezzo ancora mancante dopo API+HTML+Selenium")
            return None

        # Vecchio prezzo credibile?
        if old_price > 0 and not _is_old_price_credible(
            price=price,
            old_price=old_price,
            has_coupon=has_coupon,
            promo_code=promo_code,
            is_limited_offer=is_limited_offer,
        ):
            logger.warning(
                f"[SCRAPER] Vecchio prezzo poco credibile per ASIN {asin}: "
                f"price={price} old={old_price} discount={discount}"
            )
            old_price = 0.0
            if discount >= 85 and not has_coupon and not promo_code and not is_limited_offer:
                discount = 0.0

        # Recalcolo finale discount se vecchio prezzo è valido
        if old_price > price > 0:
            discount = round(((old_price - price) / old_price) * 100, 1)

        if discount < 0:
            discount = 0.0

        # Supporto coupon: se non c'è discount ma c'è coupon, stima lo sconto coupon.
        if discount <= 0 and has_coupon:
            coupon_boost = _estimate_coupon_discount_percent(coupon_text, price)
            discount = max(10.0, coupon_boost if coupon_boost > 0 else 10.0)

        title = clean_title(title) or "Prodotto Amazon"
        old_price_final = round(old_price, 2) if old_price > price > 0 else None
        final_price = round(price, 2)
        final_discount = round(discount, 1) if discount > 0 else 0.0
        final_link = link or f"https://www.amazon.it/dp/{asin}"

        final_product = Product(
            asin=asin,
            title=title,
            image=image,
            link=final_link,
            price=final_price,
            old_price=old_price_final,
            discount=final_discount,
            has_coupon=has_coupon,
            coupon_text=coupon_text,
            is_limited_offer=is_limited_offer,
            promo_code=promo_code,
            category=category,
        )

        logger.info(
            f"[ASIN-DETAIL] OK {asin} → price={final_product.price}, "
            f"old={final_product.old_price}, discount={final_product.discount}, "
            f"coupon={final_product.coupon_text}, promo={final_product.promo_code}, "
            f"image={'yes' if final_product.image else 'no'}, title='{final_product.title[:90]}'"
        )

        return final_product

    except Exception as e:
        logger.exception(f"[ASIN {asin}] Errore generale: {e}")
        return None
