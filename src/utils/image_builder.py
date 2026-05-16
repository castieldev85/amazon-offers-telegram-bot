import os
import re
import html
from io import BytesIO
from typing import Optional
from urllib.parse import unquote

import requests
from PIL import Image, ImageDraw, ImageFont


def _parse_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2) if float(value) > 0 else None

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/d", "nan"}:
        return None

    text = text.replace("€", " ").replace("EUR", " ").replace("eur", " ").replace("\xa0", " ")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return None

    # Formato italiano: 1.234,56 oppure 39,99
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")

    try:
        value = float(text)
        return round(value, 2) if value > 0 else None
    except Exception:
        return None


def _parse_discount(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = abs(float(value))
        return round(value, 1) if value > 0 else None

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/d", "nan"}:
        return None

    match = re.search(r"-?\d+(?:[,.]\d+)?", text)
    if not match:
        return None

    try:
        value = abs(float(match.group(0).replace(",", ".")))
        return round(value, 1) if value > 0 else None
    except Exception:
        return None


def _format_price(value) -> str:
    price = _parse_price(value)
    if price is None:
        return "N/D"
    return f"{price:.2f}".replace(".", ",") + "€"


def _format_discount(value) -> str:
    discount = _parse_discount(value)
    if discount is None:
        return ""
    if float(discount).is_integer():
        return f"-{int(discount)}%"
    return f"-{str(discount).replace('.', ',')}%"


def _old_price_is_reliable(current_price, old_price) -> bool:
    current = _parse_price(current_price)
    old = _parse_price(old_price)

    if current is None or old is None:
        return False
    if current <= 0 or old <= 0 or old <= current:
        return False

    discount_pct = ((old - current) / old) * 100.0

    # Evita prezzi vecchi palesemente falsi: es. fazzoletti 19,99€ prima 145,99€.
    if discount_pct < 5 or discount_pct > 70:
        return False

    # Limite generale: il vecchio prezzo non deve superare 3x il prezzo attuale.
    if old > current * 3.0:
        return False

    return True


def _discount_is_reliable(discount, current_price, old_price) -> bool:
    """Nella grafica mostriamo lo sconto solo se è coerente col vecchio prezzo."""
    if not _old_price_is_reliable(current_price, old_price):
        return False

    discount = _parse_discount(discount)
    current = _parse_price(current_price)
    old = _parse_price(old_price)

    if discount is None or discount <= 0 or not current or not old:
        return False

    computed = ((old - current) / old) * 100.0
    return 5 <= computed <= 70 and abs(computed - discount) <= 8


def _load_font(size: int, bold: bool = False):
    windows_fonts = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for font_path in windows_fonts:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _fit_image(img: Image.Image, size: tuple[int, int], bg=(255, 255, 255)) -> Image.Image:
    img = img.convert("RGBA")
    img.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGBA", size, bg + (255,))
    x = (size[0] - img.width) // 2
    y = (size[1] - img.height) // 2
    canvas.alpha_composite(img, (x, y))
    return canvas.convert("RGB")


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)




def _is_valid_image_url(url) -> bool:
    """Ritorna True per immagini HTTP/HTTPS o file locali esistenti."""
    if url is None:
        return False
    text = str(url).strip()
    if not text or text.lower() in {"none", "null", "nan", "n/d", "false"}:
        return False
    if text.startswith("http://") or text.startswith("https://"):
        return True
    return os.path.exists(text)


def _placeholder_product_image(size=(340, 340)) -> Image.Image:
    """Placeholder pulito quando l'offerta non ha immagine prodotto valida."""
    img = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    w, h = size

    # Box prodotto minimal
    box_w, box_h = int(w * 0.58), int(h * 0.48)
    x1 = (w - box_w) // 2
    y1 = int(h * 0.26)
    x2 = x1 + box_w
    y2 = y1 + box_h

    draw.rounded_rectangle((x1, y1, x2, y2), radius=26, fill=(247, 248, 250), outline=(222, 225, 230), width=3)
    draw.rectangle((x1 + 28, y1 - 18, x2 - 28, y1 + 18), fill=(255, 245, 230), outline=(232, 205, 160), width=2)
    draw.line((x1 + 34, y1 + 42, x2 - 34, y1 + 42), fill=(226, 229, 234), width=3)
    draw.line((x1 + 34, y1 + 72, x2 - 34, y1 + 72), fill=(226, 229, 234), width=3)

    font = _load_font(22, bold=True)
    text = "AMAZON"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, y2 + 24), text, font=font, fill=(150, 154, 162))
    return img


def _image_has_visible_content(img: Image.Image | None) -> bool:
    """Evita immagini vuote/placeholder Amazon o file trasparenti quasi bianchi."""
    if img is None:
        return False
    try:
        w, h = img.size
        if w < 80 or h < 80:
            return False
        sample = img.convert("RGB").resize((40, 40), Image.LANCZOS)
        pixels = list(sample.getdata())
        if not pixels:
            return False
        # Se oltre il 96% dei pixel è quasi bianco, per noi è una foto prodotto non valida.
        whiteish = sum(1 for r, g, b in pixels if r > 245 and g > 245 and b > 245)
        if whiteish / len(pixels) > 0.96:
            return False
        return True
    except Exception:
        return False


def _extract_image_urls_from_amazon_html(html_text: str) -> list[str]:
    """Estrae immagini prodotto da una pagina Amazon HTML.

    Serve quando PA-API non restituisce Images e l'offerta arriva da fonti Telegram.
    Cerchiamo meta og:image, landingImage, data-a-dynamic-image e hiRes/large dentro gli
    script. Le immagini vengono poi validate prima di usarle.
    """
    if not html_text:
        return []
    candidates: list[str] = []

    def add(value: str) -> None:
        value = unquote(str(value or "").strip().strip('"\\\' '))
        value = value.replace('\\/', '/')
        if value.startswith('//'):
            value = 'https:' + value
        if value.startswith('http') and value not in candidates:
            candidates.append(value)

    patterns = [
        r'<meta[^>]+property=["\\\']og:image["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\']',
        r'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:image["\\\']',
        r'id=["\\\']landingImage["\\\'][^>]+src=["\\\']([^"\\\']+)["\\\']',
        r'data-old-hires=["\\\']([^"\\\']+)["\\\']',
        r'"hiRes"\s*:\s*"([^"\\]+)"',
        r'"large"\s*:\s*"([^"\\]+)"',
        r'"mainUrl"\s*:\s*"([^"\\]+)"',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html_text, flags=re.IGNORECASE):
            add(m.group(1))

    # data-a-dynamic-image contiene spesso un JSON con URL come chiavi.
    for m in re.finditer(r'data-a-dynamic-image=["\\\']([^"\\\']+)["\\\']', html_text, flags=re.IGNORECASE):
        raw = html.unescape(m.group(1)) if 'html' in globals() else m.group(1)
        for url in re.findall(r'https?://[^"\\]+', raw):
            add(url)

    # Fallback: URL immagini m.media-amazon dentro script.
    for url in re.findall(r'https?://[^"\\\'<>\s]+(?:images/I|images/P)/[^"\\\'<>\s]+', html_text, flags=re.IGNORECASE):
        add(url)

    # Preferisci immagini medio/grandi, non sprite o pixel.
    bad_tokens = ('sprite', 'transparent-pixel', 'grey-pixel', 'blank', 'play-button', 'icon')
    out = []
    for url in candidates:
        low = url.lower()
        if any(tok in low for tok in bad_tokens):
            continue
        if url not in out:
            out.append(url)
    return out[:12]


def _amazon_page_image_candidates(asin: str) -> list[str]:
    asin = re.sub(r"[^A-Za-z0-9]", "", str(asin or "")).upper()
    if len(asin) != 10:
        return []
    page_urls = [
        f"https://www.amazon.it/dp/{asin}?th=1&psc=1",
        f"https://www.amazon.it/gp/product/{asin}?th=1&psc=1",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }
    results: list[str] = []
    for page_url in page_urls:
        try:
            resp = requests.get(page_url, timeout=18, allow_redirects=True, headers=headers)
            if resp.status_code >= 400:
                continue
            for url in _extract_image_urls_from_amazon_html(resp.text):
                if url not in results:
                    results.append(url)
            if results:
                break
        except Exception:
            continue
    return results


def _amazon_image_candidates(asin: str) -> list[str]:
    """URL fallback per immagini prodotto Amazon basate su ASIN."""
    asin = re.sub(r"[^A-Za-z0-9]", "", str(asin or "")).upper()
    if len(asin) != 10:
        return []
    candidates = [
        f"https://ws-eu.amazon-adsystem.com/widgets/q?_encoding=UTF8&ASIN={asin}&Format=_SL500_&ID=AsinImage&MarketPlace=IT&ServiceVersion=20070822&WS=1",
        f"https://m.media-amazon.com/images/P/{asin}.01._SL500_.jpg",
        f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01._SL500_.jpg",
        f"https://images-eu.ssl-images-amazon.com/images/P/{asin}.01._SL500_.jpg",
    ]
    # La pagina prodotto spesso contiene l'URL m.media-amazon reale anche quando PA-API no.
    candidates.extend(_amazon_page_image_candidates(asin))
    return candidates


def _open_image_from_value(value) -> Image.Image | None:
    """Apre un'immagine da file locale o URL. Ritorna None se non utilizzabile."""
    if not _is_valid_image_url(value):
        return None
    try:
        text = str(value).strip().strip('\"\' ')
        if os.path.exists(text):
            img = Image.open(text)
            img.load()
            return img.convert("RGBA")

        response = requests.get(
            text,
            timeout=20,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://www.amazon.it/",
            },
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type and len(response.content) > 0:
            # Se per errore ci arriva una pagina Amazon/HTML al posto di una immagine,
            # proviamo comunque a estrarre l'immagine prodotto dal markup.
            for embedded in _extract_image_urls_from_amazon_html(response.text):
                img = _open_image_from_value(embedded)
                if _image_has_visible_content(img):
                    return img
            return None
        img = Image.open(BytesIO(response.content))
        img.load()
        img = img.convert("RGBA")
        if not _image_has_visible_content(img):
            return None
        return img
    except Exception:
        return None


def _download_product_image_or_placeholder(url, asin: str = "") -> Image.Image:
    """Scarica l'immagine prodotto.

    Ordine:
    1) immagine già salvata o URL del prodotto;
    2) fallback immagine Amazon da ASIN;
    3) placeholder solo come ultima scelta.
    """
    candidates = []
    if _is_valid_image_url(url):
        candidates.append(str(url).strip())
    candidates.extend(_amazon_image_candidates(asin))

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        img = _open_image_from_value(candidate)
        if _image_has_visible_content(img):
            return img

    if os.getenv("REQUIRE_PRODUCT_IMAGE", "true").strip().lower() in {"1", "true", "yes", "on", "si", "sì"}:
        raise ValueError(f"nessuna immagine prodotto reale trovata per ASIN {asin}")

    return _placeholder_product_image()

def crea_immagine_offerta_da_url(url: str, prezzo: str, sconto: int, vecchio_prezzo: str, asin: str):
    """
    Immagine V2.2 pulita:
    - sfondo chiaro
    - prodotto grande
    - solo prezzo / vecchio prezzo / sconto se affidabili
    - niente None€
    - niente badge enormi tipo -97% quando il dato è sporco
    """
    brand_text = os.getenv("BOT_BRAND_TEXT", "t.me/amazon_offerte_sconti_coupon_top")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(base_dir)
    temp_dir = os.path.join(src_dir, "..", "temp")
    os.makedirs(temp_dir, exist_ok=True)

    # L'immagine può mancare quando l'offerta arriva da fonti Telegram o quando PA-API
    # non restituisce Images. In quel caso non dobbiamo mai bloccare la pubblicazione.
    product_img = _download_product_image_or_placeholder(url, asin)

    width, height = 900, 500
    base = Image.new("RGB", (width, height), (248, 249, 251))
    draw = ImageDraw.Draw(base)

    # Card principale
    margin = 28
    card = (margin, margin, width - margin, height - margin)
    _rounded_rect(draw, card, 34, fill=(255, 255, 255), outline=(230, 232, 236), width=2)

    # Area prodotto
    image_box = (58, 62, 438, 442)
    _rounded_rect(draw, image_box, 28, fill=(255, 255, 255), outline=(238, 238, 238), width=1)
    fitted = _fit_image(product_img, (340, 340), bg=(255, 255, 255))
    base.paste(fitted, (78, 82))

    # Testi
    font_badge = _load_font(24, bold=True)
    font_label = _load_font(24, bold=False)
    font_price = _load_font(78, bold=True)
    font_old = _load_font(34, bold=False)
    font_discount = _load_font(32, bold=True)
    font_brand = _load_font(21, bold=True)

    text_x = 495
    y = 92

    # Badge alto
    badge_text = "OFFERTA AMAZON"
    bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    badge_w = bbox[2] - bbox[0] + 36
    _rounded_rect(draw, (text_x, y, text_x + badge_w, y + 48), 24, fill=(255, 245, 230))
    draw.text((text_x + 18, y + 9), badge_text, font=font_badge, fill=(180, 92, 0))
    y += 78

    draw.text((text_x, y), "Prezzo ora", font=font_label, fill=(92, 96, 105))
    y += 36

    price_text = _format_price(prezzo)
    draw.text((text_x, y), price_text, font=font_price, fill=(22, 25, 31))
    y += 96

    current = _parse_price(prezzo)
    old = _parse_price(vecchio_prezzo)
    old_price_reliable = _old_price_is_reliable(prezzo, vecchio_prezzo)
    if old_price_reliable:
        old_text = _format_price(old)
        draw.text((text_x, y), f"Prima {old_text}", font=font_old, fill=(128, 132, 140))
        old_bbox = draw.textbbox((text_x, y), f"Prima {old_text}", font=font_old)
        line_y = (old_bbox[1] + old_bbox[3]) // 2
        draw.line((old_bbox[0], line_y, old_bbox[2], line_y), fill=(128, 132, 140), width=3)
        y += 58

    if old_price_reliable and current and old:
        computed_discount = round(((old - current) / old) * 100.0, 1)
        discount_text = _format_discount(computed_discount)
        if discount_text:
            bbox = draw.textbbox((0, 0), discount_text, font=font_discount)
            pill_w = bbox[2] - bbox[0] + 44
            _rounded_rect(draw, (text_x, y, text_x + pill_w, y + 56), 28, fill=(230, 247, 239))
            draw.text((text_x + 22, y + 9), discount_text, font=font_discount, fill=(10, 122, 71))
            y += 70

    # Niente micro-copy sotto al prezzo: l'immagine deve restare pulita.

    if brand_text:
        brand_bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
        brand_w = brand_bbox[2] - brand_bbox[0]
        draw.text((width - margin - brand_w - 18, height - 62), brand_text, font=font_brand, fill=(148, 151, 158))

    safe_asin = re.sub(r"[^A-Za-z0-9_-]", "", str(asin or "offer")) or "offer"
    output_path = os.path.join(temp_dir, f"offer_image_{safe_asin}.jpg")
    base.save(output_path, format="JPEG", quality=92, optimize=True)
    return output_path
