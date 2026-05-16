import re
import time
import logging
import requests
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.shortlink_generator import generate_affiliate_link
from src.utils.offer_scorer import estimate_final_price, parse_price

logger = logging.getLogger(__name__)


class Product:
    def __init__(
        self,
        asin,
        title,
        price,
        old_price,
        discount,
        image=None,
        category=None,
        has_coupon=False,
        coupon_text=None,
        link=None,
        promo_code=None,
        is_limited_offer=False
    ):
        self.asin = asin
        self.title = title
        self.price = price
        self.old_price = old_price
        self.discount = discount
        self.image = image
        self.category = category
        self.has_coupon = has_coupon
        self.coupon_text = coupon_text
        self.link = link
        self.promo_code = promo_code
        self.is_limited_offer = is_limited_offer

    def to_dict(self):
        return {
            "asin": self.asin,
            "title": self.title,
            "image": self.image,
            "link": self.link,
            "price": self.price,
            "old_price": self.old_price,
            "discount": self.discount,
            "category": self.category,
            "has_coupon": self.has_coupon,
            "coupon_text": self.coupon_text,
            "promo_code": self.promo_code,
            "is_limited_offer": self.is_limited_offer
        }

    @staticmethod
    def from_dict(data: dict):
        return Product(
            asin=data.get("asin"),
            title=data.get("title", "N/D"),
            image=data.get("image"),
            link=data.get("link"),
            price=data.get("price", "N/D"),
            old_price=data.get("old_price"),
            discount=data.get("discount", 0),
            category=data.get("category"),
            has_coupon=data.get("has_coupon", False),
            coupon_text=data.get("coupon_text"),
            promo_code=data.get("promo_code"),
            is_limited_offer=data.get("is_limited_offer", False)
        )


def extract_asin_from_url(url: str) -> str | None:
    if not url:
        return None

    try:
        patterns = [
            r"/dp/([A-Z0-9]{10})",
            r"/gp/product/([A-Z0-9]{10})",
            r"/product/([A-Z0-9]{10})",
        ]

        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1).upper()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }

        resp = requests.get(
            url,
            headers=headers,
            timeout=8,
            allow_redirects=True
        )
        final_url = resp.url or ""

        for pattern in patterns:
            match = re.search(pattern, final_url, re.IGNORECASE)
            if match:
                return match.group(1).upper()

    except Exception as e:
        logger.exception(f"[ASIN] Errore parsing link {url}: {e}")

    return None


def escape_md(text: str) -> str:
    """
    Escape compatibile con Telegram MarkdownV2.
    """
    if text is None:
        return ""

    text = str(text)
    return re.sub(r'([\\_*`\[\]()~>#+=|{}.!-])', r'\\\1', text)




def shorten_title(title: str, max_len: int = 95) -> str:
    """Rende i titoli Amazon più leggibili nei post Telegram."""
    title = re.sub(r"\s+", " ", _safe_str(title, "Prodotto Amazon")).strip()
    title = re.sub(r"\s*[|•–-]\s*Pagina prodotto.*$", "", title, flags=re.IGNORECASE)
    if len(title) <= max_len:
        return title
    cut = title[:max_len].rsplit(" ", 1)[0].strip()
    return (cut or title[:max_len]).rstrip(".,;- ") + "…"

def _safe_str(value, default="") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_discount_value(value) -> float:
    try:
        if value is None:
            return 0.0
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except Exception:
        return 0.0


def _format_euro(value) -> str:
    parsed = parse_price(value)
    if parsed is None:
        raw = _safe_str(value, "N/D")
        return raw if raw else "N/D"
    return f"{parsed:.2f}".replace(".", ",")


def _format_discount(value) -> str:
    discount = _safe_discount_value(value)
    if discount <= 0:
        return "0"
    if float(discount).is_integer():
        return str(int(discount))
    return f"{discount:.1f}".replace(".", ",")




# ============================================================
# Validazione qualità dato V2.4
# ============================================================
# Regola: meglio pubblicare meno dettagli, ma sempre credibili.
# Queste funzioni impediscono di mostrare prezzi vecchi assurdi
# tipo "Prima 145,99€" per fazzoletti o codici promo generici come "ARTICOLO".

STRICT_PRICE_CATEGORIES = (
    "alimentari",
    "fazzoletti",
    "carta",
    "dentifric",
    "detersiv",
    "igiene",
    "cura della persona",
    "pulizia",
    "casa",
    "cucina",
    "bellezza",
    "salute",
)

BANNED_PROMO_CODES = {
    "ARTICOLO",
    "SCONTO",
    "PROMO",
    "PROMOZIONE",
    "CODICE",
    "COUPON",
    "AMAZON",
    "OFFERA",
    "OFFERTE",
    "PRODOTTO",
    "CARRELLO",
    "APPLICA",
    "RISPARMIA",
}


def _category_is_strict(category_name: str | None) -> bool:
    cat = _safe_str(category_name, "").lower()
    return any(token in cat for token in STRICT_PRICE_CATEGORIES)


def is_reasonable_old_price(current_price, old_price, category_name: str | None = None) -> bool:
    """
    Ritorna True solo se il prezzo precedente è credibile.

    Blocca:
    - old_price mancante / non numerico
    - old_price <= prezzo attuale
    - sconti sotto 5% o sopra 70%
    - rapporti prezzo vecchio/prezzo attuale troppo alti
    """
    current = parse_price(current_price)
    old = parse_price(old_price)

    if current is None or old is None:
        return False
    if current <= 0 or old <= 0:
        return False
    if old <= current:
        return False

    discount_pct = ((old - current) / old) * 100.0
    if discount_pct < 5 or discount_pct > 70:
        return False

    max_ratio = 2.0 if _category_is_strict(category_name) else 3.0
    if old > current * max_ratio:
        return False

    return True


def get_reliable_discount_percent(current_price, old_price, fallback_discount=None, category_name: str | None = None) -> float | None:
    """
    Calcola/accetta uno sconto solo se coerente con prezzo attuale e vecchio prezzo.
    Se il vecchio prezzo non è valido, consente un fallback solo in range conservativo.
    """
    current = parse_price(current_price)
    old = parse_price(old_price)

    if is_reasonable_old_price(current, old, category_name):
        return round(((old - current) / old) * 100.0, 1)

    fallback = _safe_discount_value(fallback_discount)
    # Senza vecchio prezzo credibile non mostriamo sconti enormi: spesso sono dati rumorosi.
    if 5 <= fallback <= 60:
        return round(fallback, 1)

    return None


def is_valid_promo_code(code: str | None) -> bool:
    if not code:
        return False

    code = str(code).strip().upper()
    code = re.sub(r"\s+", "", code)

    if len(code) < 4 or len(code) > 32:
        return False
    if code in BANNED_PROMO_CODES:
        return False
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{3,31}", code):
        return False
    # Evita parole comuni prese per errore dallo scraping.
    if code.isalpha() and code in BANNED_PROMO_CODES:
        return False

    return True


def clean_promo_code(code: str | None) -> str:
    if not is_valid_promo_code(code):
        return ""
    return re.sub(r"\s+", "", str(code).strip().upper())


def clean_coupon_text(coupon_text: str | None) -> str:
    """Mostra il coupon solo se contiene un dato concreto: euro o percentuale."""
    text = _safe_str(coupon_text, "")
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()

    if low in {"coupon", "coupon disponibile", "applica coupon"}:
        return ""

    has_percent = bool(re.search(r"\d+(?:[,.]\d+)?\s*%", text))
    has_euro = bool(re.search(r"\d+(?:[,.]\d+)?\s*(?:€|euro|eur)", text, re.IGNORECASE))

    if not (has_percent or has_euro):
        return ""

    return shorten_title(text, max_len=58)

def _build_hashtag(category_name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "", _safe_str(category_name, "OfferteVarie"))
    if not clean:
        clean = "OfferteVarie"
    return "#" + clean


def _discount_is_reliable(discount_value: float, current_price=None, old_price=None) -> bool:
    """Mostra lo sconto solo quando è credibile, evitando casi tipo -97% senza prezzo vecchio."""
    try:
        discount_value = float(discount_value or 0)
    except Exception:
        return False

    if discount_value <= 0:
        return False

    current = parse_price(current_price)
    old = parse_price(old_price)

    # Sopra 80% lo mostriamo solo se il vecchio prezzo è reale e coerente.
    if discount_value > 80:
        return bool(current and old and old > current and ((old - current) / old * 100) >= 80)

    return True


def build_offer_message(product, user_id, category_name="Offerte"):
    """
    Caption V2.2 pulita per Telegram.

    Obiettivo:
    - meno righe
    - niente valori sporchi tipo None€
    - niente -97% se il dato non è affidabile
    - CTA chiara solo nel pulsante
    """
    asin = _safe_str(getattr(product, "asin", ""))

    if not asin and getattr(product, "link", None):
        asin = extract_asin_from_url(product.link) or ""

    shortlink = None
    if asin:
        try:
            shortlink = generate_affiliate_link(user_id, asin)
        except Exception as e:
            logger.exception(f"[AFFILIATE] Errore generazione shortlink per ASIN {asin}: {e}")

    if not shortlink:
        if getattr(product, "link", None):
            shortlink = product.link
        elif asin:
            shortlink = f"https://www.amazon.it/dp/{asin}"
        else:
            shortlink = "https://www.amazon.it/"

    category_name = category_name or getattr(product, "category", None) or "Offerte"
    category_name = _safe_str(category_name, "Offerte")

    offer_info = estimate_final_price(product)
    current_price = offer_info.get("current_price") or parse_price(getattr(product, "price", None))
    old_price = offer_info.get("old_price") or parse_price(getattr(product, "old_price", None))
    estimated_final_price = offer_info.get("estimated_final_price")
    total_estimated_discount = offer_info.get("total_estimated_discount_percent")

    coupon_info = offer_info.get("coupon_info", {}) or {}
    promo_info = offer_info.get("promo_info", {}) or {}

    title = escape_md(shorten_title(getattr(product, "title", "Prodotto Amazon"), max_len=78))
    category = escape_md(category_name)
    hashtag = escape_md(_build_hashtag(category_name))
    pub_tag = escape_md("#pubblicità")

    price_text = escape_md(_format_euro(current_price if current_price is not None else getattr(product, "price", None)))
    old_price_is_reliable = is_reasonable_old_price(current_price, old_price, category_name)
    reliable_discount = get_reliable_discount_percent(
        current_price,
        old_price,
        getattr(product, "discount", None),
        category_name,
    )

    old_price_text = escape_md(_format_euro(old_price)) if old_price_is_reliable else ""
    final_price_text = escape_md(_format_euro(estimated_final_price)) if estimated_final_price is not None else ""

    coupon_text_raw = clean_coupon_text(getattr(product, "coupon_text", None) or coupon_info.get("coupon_text"))
    has_coupon = bool(coupon_text_raw)

    promo_code_raw = clean_promo_code(getattr(product, "promo_code", None) or promo_info.get("promo_code"))
    promo_code = escape_md(promo_code_raw)

    show_old_price = old_price_is_reliable
    show_final_price = bool(
        estimated_final_price is not None
        and current_price is not None
        and estimated_final_price > 0
        and estimated_final_price < current_price
        and (has_coupon or promo_code_raw)
    )

    # Header breve e meno “urlato”.
    message = f"🔥 *Offerta Amazon*\n\n*{title}*\n\n"

    if show_final_price:
        message += f"💶 *Prezzo:* *{final_price_text}€*"
        message += f"  invece di *{price_text}€*\n"
    else:
        message += f"💶 *Prezzo:* *{price_text}€*\n"

    if show_old_price:
        message += f"📉 Prima: ~{old_price_text}€~"
        if reliable_discount:
            base_discount_text = escape_md(_format_discount(reliable_discount))
            message += f"  \\(\\-{base_discount_text}%\\)"
        message += "\n"
    elif reliable_discount:
        base_discount_text = escape_md(_format_discount(reliable_discount))
        message += f"📉 Sconto: \\-{base_discount_text}%\n"

    if has_coupon:
        coupon_text = escape_md(coupon_text_raw)
        message += f"🏷️ Coupon: {coupon_text}\n"

    if promo_code_raw:
        message += f"🎟️ Codice: `{promo_code}`\n"

    # Mostriamo lo sconto totale solo se aggiunge valore rispetto allo sconto base.
    # Evita doppioni tipo: Prima -21,4% + Risparmio stimato -21,4%.
    if (
        total_estimated_discount is not None
        and total_estimated_discount > 0
        and total_estimated_discount <= 70
        and show_final_price
        and (has_coupon or promo_code_raw)
        and (not reliable_discount or float(total_estimated_discount) >= float(reliable_discount) + 3)
    ):
        total_discount_text = escape_md(_format_discount(total_estimated_discount))
        message += f"🚀 Risparmio stimato: \\-{total_discount_text}%\n"

    if bool(getattr(product, "is_limited_offer", False)):
        message += "⏰ Offerta a tempo\n"

    message += f"\n📦 {category}  •  {hashtag}  •  {pub_tag}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Apri offerta Amazon", url=shortlink)]
    ])

    return message, keyboard

def fetch_product_details_from_asins(asins: list) -> list[Product]:
    """
    Riceve una lista di ASIN e restituisce una lista di oggetti Product completi.
    Fallback tramite Selenium scraping.
    """
    from src.utils.extract_product_info_selenium import extract_product_info_selenium

    products = []

    if not asins:
        return products

    for asin in asins:
        try:
            if not asin:
                continue

            asin = str(asin).strip().upper()
            url = f"https://www.amazon.it/dp/{asin}"

            product = extract_product_info_selenium(url)

            if product and getattr(product, "title", None) and getattr(product, "price", None) not in (None, "", "N/D"):
                products.append(product)

            time.sleep(1)

        except Exception as e:
            logger.exception(f"[fetch_product_details_from_asins] Errore su ASIN {asin}: {e}")

    return products