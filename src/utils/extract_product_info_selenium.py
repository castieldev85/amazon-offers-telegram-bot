import time
import re
import os
import random
import logging

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from src.utils.product import Product, extract_asin_from_url

logger = logging.getLogger(__name__)


def _clean_price_text(value: str) -> str:
    """
    Normalizza una stringa prezzo Amazon in formato semplice tipo:
    1299,99 -> 1299.99
    """
    if not value:
        return "N/D"

    text = str(value)
    text = text.replace("\n", " ").replace("\xa0", " ").strip()
    text = text.replace("€", "").replace("EUR", "").strip()

    # Tieni solo cifre, punti e virgole
    text = re.sub(r"[^0-9,\.]", "", text)

    # Caso tipo "1.299,99" -> "1299.99"
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")

    # Caso tipo "1299,99" -> "1299.99"
    elif "," in text:
        text = text.replace(",", ".")

    # Rimuovi eventuali punti multipli strani
    parts = text.split(".")
    if len(parts) > 2:
        text = "".join(parts[:-1]) + "." + parts[-1]

    return text if text else "N/D"


def _safe_float(value):
    try:
        if value in (None, "", "N/D"):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _is_amazon_block_page(driver) -> bool:
    """
    Rileva blocchi comuni di Amazon: captcha / robot / verifica accesso.
    """
    try:
        page_source = (driver.page_source or "").lower()
        title = (driver.title or "").lower()

        signals = [
            "captcha",
            "enter the characters you see below",
            "sorry, we just need to make sure you're not a robot",
            "not a robot",
            "robot check",
        ]

        return any(sig in page_source or sig in title for sig in signals)
    except Exception:
        return False


def start_selenium():
    chromium_service = Service()

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.add_argument("--lang=it-IT")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    if os.environ.get("REMOTE_CHROMIUM"):
        driver = webdriver.Remote(
            command_executor=os.environ["REMOTE_CHROMIUM"],
            options=options
        )
        driver.command_executor._conn.timeout = 300
    else:
        driver = webdriver.Chrome(service=chromium_service, options=options)

    driver.set_page_load_timeout(60)
    return driver


def _try_accept_cookies(driver):
    cookie_selectors = [
        (By.ID, "sp-cc-accept"),
        (By.NAME, "accept"),
        (By.CSS_SELECTOR, "input#sp-cc-accept"),
    ]

    for by, selector in cookie_selectors:
        try:
            button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, selector))
            )
            button.click()
            time.sleep(1)
            return
        except Exception:
            continue


def _extract_title(driver) -> str:
    selectors = [
        (By.ID, "productTitle"),
        (By.CSS_SELECTOR, "#productTitle"),
        (By.CSS_SELECTOR, "span#productTitle"),
    ]

    for by, selector in selectors:
        try:
            el = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by, selector))
            )
            text = el.text.strip()
            if text:
                return text
        except Exception:
            continue

    return "Titolo non trovato"


def _text_or_attr(driver, by, selector) -> str:
    try:
        el = driver.find_element(by, selector)
        txt = (el.text or "").strip()
        if not txt:
            txt = (el.get_attribute("textContent") or el.get_attribute("innerText") or el.get_attribute("aria-label") or "").strip()
        return txt
    except Exception:
        return ""


def _extract_json_ld_price(driver) -> str:
    try:
        import json
        scripts = driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
        for script in scripts:
            raw = script.get_attribute("textContent") or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                offers = block.get("offers")
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    for key in ("price", "lowPrice", "highPrice"):
                        val = offers.get(key)
                        cleaned = _clean_price_text(str(val or ""))
                        if _safe_float(cleaned) is not None:
                            return cleaned
    except Exception:
        pass
    return "N/D"


def _extract_price(driver) -> str:
    # Tentativi ordinati dal più affidabile al meno affidabile.
    # Usiamo anche textContent perché .a-offscreen spesso è nascosto e Selenium .text torna vuoto.
    selectors = [
        (By.CSS_SELECTOR, "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen"),
        (By.CSS_SELECTOR, "#corePrice_feature_div span.a-price span.a-offscreen"),
        (By.CSS_SELECTOR, "#apex_desktop span.a-price span.a-offscreen"),
        (By.CSS_SELECTOR, ".a-price.aok-align-center .a-offscreen"),
        (By.CSS_SELECTOR, ".apexPriceToPay span.a-offscreen"),
        (By.CSS_SELECTOR, ".a-price .a-offscreen"),
        (By.ID, "priceblock_ourprice"),
        (By.ID, "priceblock_dealprice"),
        (By.ID, "price_inside_buybox"),
    ]

    for by, selector in selectors:
        txt = _text_or_attr(driver, by, selector)
        value = _clean_price_text(txt)
        if value and value != "N/D" and _safe_float(value) is not None:
            return value

    try:
        whole = _text_or_attr(driver, By.CSS_SELECTOR, ".a-price .a-price-whole")
        fraction = _text_or_attr(driver, By.CSS_SELECTOR, ".a-price .a-price-fraction")
        if whole:
            value = _clean_price_text(f"{whole},{fraction or '00'}")
            if value and value != "N/D" and _safe_float(value) is not None:
                return value
    except Exception:
        pass

    json_price = _extract_json_ld_price(driver)
    if json_price != "N/D":
        return json_price

    return "N/D"

def _extract_old_price(driver, current_price: str) -> str:
    selectors = [
        ".a-price.a-text-price .a-offscreen",
        "span.priceBlockStrikePriceString",
        ".basisPrice .a-offscreen",
        "td.a-span12 span.a-text-price span.a-offscreen",
        ".a-text-price span.a-offscreen",
    ]

    current_val = _safe_float(current_price)

    candidates = []

    for sel in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                raw_txt = (el.text or el.get_attribute("textContent") or el.get_attribute("innerText") or el.get_attribute("aria-label") or "")
                txt = _clean_price_text(raw_txt)
                val = _safe_float(txt)
                if val is not None:
                    candidates.append((txt, val))
        except Exception:
            continue

    # scegli il prezzo maggiore del corrente come vecchio prezzo
    if current_val is not None:
        valid = [(txt, val) for txt, val in candidates if val > current_val]
        if valid:
            valid.sort(key=lambda x: x[1], reverse=True)
            return valid[0][0]

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    return ""


def _extract_discount(driver, price: str, old_price: str) -> int:
    price_val = _safe_float(price)
    old_price_val = _safe_float(old_price)

    if price_val is not None and old_price_val is not None and old_price_val > price_val and old_price_val > 0:
        try:
            return int(round((old_price_val - price_val) / old_price_val * 100))
        except Exception:
            pass

    # Badge sconto
    badge_selectors = [
        ".savingsPercentage",
        ".dealBadgePercent",
        ".a-size-large.a-color-price",
        ".reinventPriceSavingsPercentageMargin",
    ]

    for sel in badge_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                txt = el.text.strip()
                m = re.search(r"(\d+)\s*%", txt)
                if m:
                    return int(m.group(1))
        except Exception:
            continue

    # Risparmi X€
    try:
        save_texts = driver.find_elements(By.XPATH, "//*[contains(text(), 'Risparmi')]")
        for el in save_texts:
            txt = el.text
            m_eur = re.search(r"Risparmi\s*([0-9,.]+)\s*€", txt)
            if m_eur and price_val is not None:
                risp = float(m_eur.group(1).replace(",", "."))
                old_price_calc = price_val + risp
                if old_price_calc > 0:
                    return int(round((old_price_calc - price_val) / old_price_calc * 100))
    except Exception:
        pass

    return 0


def _extract_image(driver) -> str:
    selectors = [
        (By.ID, "landingImage"),
        (By.CSS_SELECTOR, "#landingImage"),
        (By.CSS_SELECTOR, "#imgTagWrapperId img"),
        (By.CSS_SELECTOR, "img.a-dynamic-image"),
    ]

    for by, selector in selectors:
        try:
            el = driver.find_element(by, selector)
            src = el.get_attribute("data-old-hires") or el.get_attribute("src") or ""
            if not src:
                raw_dynamic = el.get_attribute("data-a-dynamic-image") or ""
                if raw_dynamic:
                    try:
                        import json
                        data = json.loads(raw_dynamic)
                        if isinstance(data, dict) and data:
                            src = max(data.keys(), key=len)
                    except Exception:
                        pass
            if src:
                return src.strip()
        except Exception:
            continue

    return ""


def _extract_coupon_info(driver) -> tuple[bool, str | None]:
    has_coupon = False
    promo_code = None

    try:
        coupon_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Coupon')]")
        if coupon_elements:
            has_coupon = True

        for el in coupon_elements:
            txt = (el.text or "").strip()
            if not txt:
                continue

            m_code = re.search(r"\b[A-Z0-9]{5,}\b", txt)
            if m_code:
                promo_code = m_code.group(0)
                break
    except Exception:
        pass

    return has_coupon, promo_code


def _extract_limited_offer(driver) -> bool:
    selectors = [
        (By.ID, "dealBadge"),
        (By.CSS_SELECTOR, "#dealBadge"),
        (By.XPATH, "//*[contains(text(), 'Offerta a tempo')]"),
        (By.XPATH, "//*[contains(text(), 'Limited time deal')]"),
    ]

    for by, selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
            if elements:
                return True
        except Exception:
            continue

    return False


def extract_product_info_selenium(url: str) -> Product | None:
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        driver = None
        try:
            logger.info(f"[SCRAPER] Tentativo {attempt}/{max_retries} su {url}")

            driver = start_selenium()
            driver.get(url)

            time.sleep(random.uniform(1.8, 3.0))
            _try_accept_cookies(driver)

            if _is_amazon_block_page(driver):
                logger.warning(f"[SCRAPER] Pagina bloccata/CAPTCHA rilevata su {url}")
                raise RuntimeError("Amazon block page detected")

            # piccolo scroll per attivare lazy-load
            try:
                driver.execute_script("window.scrollBy(0, 600)")
                time.sleep(1)
            except Exception:
                pass

            title = _extract_title(driver)
            price = _extract_price(driver)
            old_price = _extract_old_price(driver, price)
            discount = _extract_discount(driver, price, old_price)
            image_url = _extract_image(driver)
            has_coupon, promo_code = _extract_coupon_info(driver)
            is_limited_offer = _extract_limited_offer(driver)

            asin = extract_asin_from_url(url) or ""

            logger.info(
                f"[SCRAPER] OK asin={asin} title={title[:60]!r} "
                f"price={price} old_price={old_price} discount={discount}"
            )

            return Product(
                asin=asin,
                title=title,
                price=price,
                old_price=old_price,
                discount=discount,
                image=image_url,
                has_coupon=has_coupon,
                link=url,
                promo_code=promo_code,
                is_limited_offer=is_limited_offer,
                category=None
            )

        except Exception as e:
            logger.exception(f"[SCRAPER ERROR] Tentativo {attempt}/{max_retries} su {url}: {e}")
            time.sleep(2 * attempt)

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        time.sleep(random.uniform(1.5, 3.5))

    logger.error(f"[SCRAPER ERROR] Falliti tutti i tentativi su {url}")
    return None