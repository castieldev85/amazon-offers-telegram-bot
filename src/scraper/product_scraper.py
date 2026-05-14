import time
import re
import random
import logging
import requests
from bs4 import BeautifulSoup

from src.utils.product import Product
from src.utils.amazon_api_helper import fetch_product_details_from_api

logger = logging.getLogger(__name__)


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
    Estrae i dati di un prodotto usando:
    1) Amazon PA-API
    2) fallback HTML solo se il prezzo API manca o il titolo è sporco
    3) lettura coupon/promo HTML solo come arricchimento
    """
    try:
        product = fetch_product_details_from_api(asin)

        if not product:
            logger.error(f"[API] Nessun dato valido per ASIN {asin}")
            return None

        # Dati base API
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

        # Fallback titolo se sporco
        if not title or _contains_title_noise(title) or len(title) < 8:
            html_title = extract_title_from_html(asin)
            if html_title:
                logger.info(f"[HTML] Titolo migliorato per ASIN {asin}")
                title = html_title

        # Fallback HTML prezzo solo se il prezzo API manca
        if price <= 0:
            logger.warning(f"[API] Prezzo mancante → fallback HTML per ASIN {asin}")

            html_price, html_old = extract_price_from_html(asin)

            if not html_price or html_price <= 0:
                logger.error(f"[HTML] Nessun prezzo trovato → scarto ASIN {asin}")
                return None

            price = html_price

            if html_old and html_old > price:
                old_price = html_old
            elif old_price <= price:
                old_price = 0.0

            if old_price > price > 0:
                discount = round(((old_price - price) / old_price) * 100, 1)
            else:
                discount = 0.0

        # Arricchimento coupon HTML
        try:
            html_has_coupon, html_coupon_text = extract_coupon_from_html(asin)
            if html_has_coupon:
                has_coupon = True
                coupon_text = clean_coupon_text(html_coupon_text) or html_coupon_text
        except Exception as e:
            logger.warning(f"[COUPON] Impossibile leggere coupon per ASIN {asin}: {e}")

        # Arricchimento promo code HTML
        try:
            if not promo_code:
                html_promo_code = extract_promo_code_from_html(asin)
                if html_promo_code:
                    promo_code = html_promo_code
        except Exception as e:
            logger.warning(f"[PROMO] Impossibile leggere promo code per ASIN {asin}: {e}")

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

            # Se lo sconto era basato su un vecchio prezzo assurdo, azzeralo
            if discount >= 85 and not has_coupon and not promo_code and not is_limited_offer:
                discount = 0.0

        # Recalcolo finale discount se vecchio prezzo è valido
        if old_price > price > 0:
            discount = round(((old_price - price) / old_price) * 100, 1)

        if discount < 0:
            discount = 0.0

        # Supporto coupon: se non c'è discount ma c'è coupon, alza il minimo per farlo entrare nel buffer
        if discount <= 0 and has_coupon:
            coupon_boost = _estimate_coupon_discount_percent(coupon_text, price)
            discount = max(10.0, coupon_boost if coupon_boost > 0 else 10.0)

        # Normalizzazione finale
        title = clean_title(title) or "Prodotto Amazon"
        old_price_final = round(old_price, 2) if old_price > price > 0 else None
        final_price = round(price, 2) if price > 0 else 0.0
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
            f"[DEBUG] {asin} → price={final_product.price}, "
            f"old={final_product.old_price}, discount={final_product.discount}, "
            f"coupon={final_product.coupon_text}, promo={final_product.promo_code}, "
            f"title='{final_product.title[:90]}'"
        )

        return final_product

    except Exception as e:
        logger.exception(f"[ASIN {asin}] Errore generale: {e}")
        return None
