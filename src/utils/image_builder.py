import os
import re
from io import BytesIO
from typing import Optional

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

    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    product_img = Image.open(BytesIO(response.content)).convert("RGBA")

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
