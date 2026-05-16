import math
import re
from typing import Any, Dict, Optional


CATEGORY_BONUS = {
    "cat_elettronica": 8,
    "cat_casa_cucina": 10,
    "cat_bellezza": 12,
    "cat_sport": 9,
    "cat_auto_moto": 8,
    "cat_abbigliamento": 7,
    "cat_giocattoli": 6,
    "cat_faidate": 7,
    "cat_alimentari": 8,
    "cat_animali": 7,
    "cat_videogiochi": 4,
    "cat_libri": 1,
    "cat_deals": 10,
    "cat_all": 5,
    "cat_goldbox": 10,
}

NOISE_PATTERNS = [
    r"pagina successiva",
    r"prodotti sponsorizzati",
    r"prodotti sponsorizzati simili",
    r"specifiche del prodotto",
    r"produttore",
    r"classifica bestseller",
    r"posizione nella classifica",
]


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_text(value: Any) -> str:
    text = _safe_str(value).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _contains_noise(text: str) -> bool:
    t = _normalize_text(text)
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def parse_price(value: Any) -> Optional[float]:
    """
    Converte stringhe tipo:
    - "39,99"
    - "39,99€"
    - "EUR 39,99"
    - 39.99
    in float.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        if math.isnan(float(value)) or float(value) <= 0:
            return None
        return round(float(value), 2)

    text = _safe_str(value)
    if not text:
        return None

    text = text.replace("€", " ")
    text = text.replace("EUR", " ")
    text = text.replace("eur", " ")
    text = text.replace("\xa0", " ")
    text = text.strip()

    # Rimuove punti migliaia e gestisce la virgola decimale
    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text:
        return None

    # Caso italiano: 1.234,56
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        # Caso 39,99
        text = text.replace(",", ".")

    try:
        val = float(text)
        if val <= 0:
            return None
        return round(val, 2)
    except Exception:
        return None


def parse_percent(value: Any) -> Optional[float]:
    """
    Converte:
    - "15%"
    - "-15%"
    - 15
    in float percentuale positiva.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        v = abs(float(value))
        return round(v, 2) if v > 0 else None

    text = _safe_str(value)
    if not text:
        return None

    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", text)
    if not m:
        # prova anche numero semplice
        m2 = re.search(r"(-?\d+(?:[.,]\d+)?)", text)
        if not m2:
            return None
        text_num = m2.group(1).replace(",", ".")
    else:
        text_num = m.group(1).replace(",", ".")

    try:
        v = abs(float(text_num))
        return round(v, 2) if v > 0 else None
    except Exception:
        return None




BANNED_PROMO_CODES = {
    "ARTICOLO", "SCONTO", "PROMO", "PROMOZIONE", "CODICE", "COUPON",
    "AMAZON", "OFFERA", "OFFERTE", "PRODOTTO", "CARRELLO", "APPLICA", "RISPARMIA",
}


def is_valid_promo_code(code: Any) -> bool:
    code = _safe_str(code).upper()
    code = re.sub(r"\s+", "", code)
    if len(code) < 4 or len(code) > 32:
        return False
    if code in BANNED_PROMO_CODES:
        return False
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{3,31}", code))


def clean_promo_code(code: Any) -> str:
    if not is_valid_promo_code(code):
        return ""
    return re.sub(r"\s+", "", _safe_str(code).upper())


def is_reasonable_old_price(current_price: Any, old_price: Any, category: Optional[str] = None) -> bool:
    current = parse_price(current_price)
    old = parse_price(old_price)
    if current is None or old is None:
        return False
    if current <= 0 or old <= 0 or old <= current:
        return False
    discount_pct = ((old - current) / old) * 100.0
    if discount_pct < 5 or discount_pct > 70:
        return False
    cat = _normalize_text(category or "")
    strict = any(x in cat for x in ["alimentari", "fazzoletti", "carta", "dentifric", "detersiv", "igiene", "pulizia", "casa", "cucina"])
    max_ratio = 2.0 if strict else 3.0
    return old <= current * max_ratio

def extract_coupon_info(product) -> Dict[str, Any]:
    """
    Cerca di capire se il prodotto ha un coupon e di che tipo.
    Campi provati:
    - product.has_coupon
    - product.coupon_text
    - product.coupon
    - product.coupon_label
    - product.coupon_raw
    """
    has_coupon = bool(getattr(product, "has_coupon", False))

    raw_candidates = [
        getattr(product, "coupon_text", None),
        getattr(product, "coupon", None),
        getattr(product, "coupon_label", None),
        getattr(product, "coupon_raw", None),
    ]

    raw_text = ""
    for item in raw_candidates:
        item_str = _safe_str(item)
        if item_str:
            raw_text = item_str
            break

    coupon_text = _normalize_text(raw_text)

    if coupon_text and _contains_noise(coupon_text):
        coupon_text = ""

    coupon_type = None
    coupon_value = None

    if coupon_text:
        # Esempi:
        # "Risparmia 15% con coupon"
        # "Coupon 5€"
        # "Applica coupon da 10 euro"
        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", coupon_text)
        euro_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(€|euro)", coupon_text)

        if percent_match:
            coupon_type = "percent"
            coupon_value = parse_percent(percent_match.group(1))
            has_coupon = True
        elif euro_match:
            coupon_type = "fixed"
            coupon_value = parse_price(euro_match.group(1))
            has_coupon = True
        elif "coupon" in coupon_text:
            has_coupon = True

    return {
        "has_coupon": has_coupon,
        "coupon_text": raw_text,
        "coupon_type": coupon_type,
        "coupon_value": coupon_value,
    }


def extract_promo_info(product) -> Dict[str, Any]:
    """
    Legge dati promo da eventuali campi:
    - promo_code
    - promo_discount_percent
    - promo_discount_value
    - promo_text
    """
    promo_code = clean_promo_code(getattr(product, "promo_code", None))
    promo_text = _safe_str(getattr(product, "promo_text", None))

    promo_percent = parse_percent(getattr(product, "promo_discount_percent", None))
    promo_value = parse_price(getattr(product, "promo_discount_value", None))

    # Fallback: prova a leggere promo_text
    text = _normalize_text(promo_text)
    if text:
        if promo_percent is None:
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
            if m:
                promo_percent = parse_percent(m.group(1))
        if promo_value is None:
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(€|euro)", text)
            if m:
                promo_value = parse_price(m.group(1))

    return {
        "has_promo_code": bool(promo_code),
        "promo_code": promo_code,
        "promo_percent": promo_percent,
        "promo_value": promo_value,
        "promo_text": promo_text,
    }


def estimate_final_price(product) -> Dict[str, Any]:
    """
    Stima il prezzo finale applicando:
    1) coupon
    2) promo code
    sul prezzo attuale visibile.

    Restituisce anche sconto totale stimato vs vecchio prezzo, se disponibile.
    """
    current_price = parse_price(getattr(product, "price", None))
    old_price = parse_price(getattr(product, "old_price", None))
    base_discount_percent = parse_percent(getattr(product, "discount", None)) or 0.0

    coupon_info = extract_coupon_info(product)
    promo_info = extract_promo_info(product)

    if current_price is None:
        return {
            "current_price": None,
            "old_price": old_price,
            "estimated_final_price": None,
            "base_discount_percent": base_discount_percent,
            "coupon_amount": 0.0,
            "coupon_percent": coupon_info["coupon_value"] if coupon_info["coupon_type"] == "percent" else None,
            "promo_amount": 0.0,
            "promo_percent": promo_info["promo_percent"],
            "total_estimated_discount_percent": None,
            "coupon_info": coupon_info,
            "promo_info": promo_info,
        }

    running_price = current_price
    coupon_amount = 0.0
    promo_amount = 0.0

    # Coupon
    if coupon_info["has_coupon"]:
        if coupon_info["coupon_type"] == "percent" and coupon_info["coupon_value"]:
            coupon_amount = running_price * (coupon_info["coupon_value"] / 100.0)
            running_price -= coupon_amount
        elif coupon_info["coupon_type"] == "fixed" and coupon_info["coupon_value"]:
            coupon_amount = min(running_price, coupon_info["coupon_value"])
            running_price -= coupon_amount

    # Promo code
    if promo_info["has_promo_code"] or promo_info["promo_percent"] or promo_info["promo_value"]:
        if promo_info["promo_percent"]:
            promo_amount = running_price * (promo_info["promo_percent"] / 100.0)
            running_price -= promo_amount
        elif promo_info["promo_value"]:
            promo_amount = min(running_price, promo_info["promo_value"])
            running_price -= promo_amount

    estimated_final = max(round(running_price, 2), 0.0)

    total_estimated_discount_percent = None
    if old_price and old_price > 0 and estimated_final < old_price and is_reasonable_old_price(current_price, old_price):
        total_estimated_discount_percent = round(((old_price - estimated_final) / old_price) * 100.0, 2)
    elif current_price and current_price > 0 and estimated_final < current_price:
        total_estimated_discount_percent = round(((current_price - estimated_final) / current_price) * 100.0, 2)

    return {
        "current_price": current_price,
        "old_price": old_price,
        "estimated_final_price": estimated_final,
        "base_discount_percent": base_discount_percent,
        "coupon_amount": round(coupon_amount, 2),
        "coupon_percent": coupon_info["coupon_value"] if coupon_info["coupon_type"] == "percent" else None,
        "promo_amount": round(promo_amount, 2),
        "promo_percent": promo_info["promo_percent"],
        "total_estimated_discount_percent": total_estimated_discount_percent,
        "coupon_info": coupon_info,
        "promo_info": promo_info,
    }


def score_super_offer(product, category: Optional[str] = None) -> float:
    """
    Score per trovare vere super offerte con coupon/promo.
    Più alto = meglio.
    """
    info = estimate_final_price(product)

    current_price = info["current_price"]
    old_price = info["old_price"]
    estimated_final = info["estimated_final_price"]
    base_discount = info["base_discount_percent"] or 0.0
    total_discount = info["total_estimated_discount_percent"] or 0.0

    coupon_info = info["coupon_info"]
    promo_info = info["promo_info"]

    score = 0.0

    # Sconto base visibile: lo premiamo solo se è plausibile.
    if current_price and is_reasonable_old_price(current_price, info["old_price"], category):
        score += base_discount * 1.15

    # Sconto totale stimato
    score += total_discount * 1.40

    # Coupon
    if coupon_info["has_coupon"]:
        score += 14

    if coupon_info["coupon_type"] == "percent" and coupon_info["coupon_value"]:
        score += coupon_info["coupon_value"] * 1.3

    if coupon_info["coupon_type"] == "fixed" and info["coupon_amount"]:
        score += min(info["coupon_amount"] * 2.0, 20)

    # Promo code
    if promo_info["has_promo_code"]:
        score += 18

    if promo_info["promo_percent"]:
        score += promo_info["promo_percent"] * 1.4

    if promo_info["promo_value"]:
        score += min(promo_info["promo_value"] * 2.2, 25)

    # Offerta lampo / limited offer
    if bool(getattr(product, "is_limited_offer", False)):
        score += 10

    # Bonus prezzo finale interessante
    if estimated_final is not None:
        if estimated_final <= 10:
            score += 12
        elif estimated_final <= 20:
            score += 10
        elif estimated_final <= 35:
            score += 8
        elif estimated_final <= 60:
            score += 5

    # Bonus se vecchio prezzo credibile esiste
    if old_price and current_price and is_reasonable_old_price(current_price, old_price, category):
        score += 6

    # Bonus categoria
    if category:
        score += CATEGORY_BONUS.get(category, 0)

    # Malus dati incompleti
    if current_price is None:
        score -= 25

    if not _safe_str(getattr(product, "title", None)):
        score -= 10

    # Malus se il titolo sembra troppo sporco/spammy
    title = _normalize_text(getattr(product, "title", ""))
    if len(title) < 8:
        score -= 8

    # Malus se non c'è nessun vero segnale forte
    strong_signal = (
        coupon_info["has_coupon"]
        or promo_info["has_promo_code"]
        or bool(promo_info["promo_percent"])
        or bool(promo_info["promo_value"])
        or total_discount >= 35
    )
    if not strong_signal:
        score -= 12

    return round(score, 2)



def get_effective_discount_percent(product, category: Optional[str] = None) -> float:
    """
    Restituisce lo sconto realmente utilizzabile per i filtri utente.

    Regola V3.2:
    - il filtro sconto impostato dall'utente deve essere obbligatorio;
    - un prodotto con score alto non può bypassare il filtro sconto;
    - se il vecchio prezzo non è credibile, lo sconto base viene considerato 0;
    - coupon/promo vengono contati solo se abbassano davvero il prezzo finale.
    """
    info = estimate_final_price(product)

    current_price = info.get("current_price")
    old_price = info.get("old_price")
    estimated_final = info.get("estimated_final_price")

    if current_price is None or current_price <= 0:
        return 0.0

    old_is_reliable = bool(old_price and is_reasonable_old_price(current_price, old_price, category))

    base_discount = 0.0
    if old_is_reliable:
        base_discount = ((old_price - current_price) / old_price) * 100.0

    total_discount = base_discount

    if estimated_final is not None and estimated_final > 0 and estimated_final < current_price:
        if old_is_reliable:
            total_discount = ((old_price - estimated_final) / old_price) * 100.0
        else:
            total_discount = ((current_price - estimated_final) / current_price) * 100.0

    if total_discount < 0:
        return 0.0
    if total_discount > 100:
        return 0.0

    return round(total_discount, 2)


def passes_user_min_discount(product, min_discount: int | float, category: Optional[str] = None) -> bool:
    """True solo se l'offerta rispetta il filtro sconto impostato dall'utente."""
    try:
        required = float(min_discount or 0)
    except Exception:
        required = 0.0

    if required <= 0:
        return True

    return get_effective_discount_percent(product, category=category) >= required

def is_super_offer(product, category: Optional[str] = None, threshold: float = 55.0) -> bool:
    return score_super_offer(product, category=category) >= threshold


def build_offer_debug_summary(product, category: Optional[str] = None) -> str:
    info = estimate_final_price(product)
    score = score_super_offer(product, category=category)

    coupon_info = info["coupon_info"]
    promo_info = info["promo_info"]
    effective_discount = get_effective_discount_percent(product, category=category)

    return (
        f"score={score} | "
        f"effective_discount={effective_discount} | "
        f"price={info['current_price']} | "
        f"old={info['old_price']} | "
        f"final={info['estimated_final_price']} | "
        f"base_discount={info['base_discount_percent']} | "
        f"total_discount={info['total_estimated_discount_percent']} | "
        f"coupon={coupon_info['has_coupon']} {coupon_info['coupon_type']} {coupon_info['coupon_value']} | "
        f"promo_code={promo_info['has_promo_code']} | "
        f"promo_percent={promo_info['promo_percent']} | "
        f"promo_value={promo_info['promo_value']}"
    )