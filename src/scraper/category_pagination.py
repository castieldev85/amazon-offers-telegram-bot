from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urljoin

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import MaxRetryError

from src.configs.scraper_settings import MAX_SCROLLS, SCROLL_DELAY, SCROLL_AMOUNT_PX, DEFAULT_CATEGORY_MAX_PAGES

logger = logging.getLogger(__name__)

_ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/?&#]|$)", re.IGNORECASE)


def start_selenium():
    chromium_service = Service()
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=it-IT")
    options.add_argument("--enable-unsafe-swiftshader")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    if os.environ.get("REMOTE_CHROMIUM"):
        try:
            driver = webdriver.Remote(command_executor=os.environ["REMOTE_CHROMIUM"], options=options)
        except MaxRetryError:
            driver = webdriver.Remote(command_executor=os.environ["REMOTE_CHROMIUM"] + "/wd/hub", options=options)
        driver.command_executor._conn.timeout = 300
    else:
        driver = webdriver.Chrome(service=chromium_service, options=options)

    driver.set_page_load_timeout(120)
    driver.implicitly_wait(6)
    return driver


def _is_block_page(driver) -> bool:
    try:
        html = (driver.page_source or "").lower()
        title = (driver.title or "").lower()
        signals = [
            "captcha",
            "robot check",
            "not a robot",
            "enter the characters you see below",
            "sorry, we just need to make sure you're not a robot",
        ]
        return any(sig in html or sig in title for sig in signals)
    except Exception:
        return False


def _accept_cookies(driver):
    selectors = [
        (By.ID, "sp-cc-accept"),
        (By.NAME, "accept"),
        (By.CSS_SELECTOR, "input#sp-cc-accept"),
        (By.XPATH, "//input[contains(@aria-labelledby,'accept') or contains(@id,'accept')]"),
    ]
    for selector in selectors:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable(selector))
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(1)
            logger.info("✅ Cookie accettati.")
            return
        except Exception:
            continue
    logger.info("ℹ️ Nessun cookie da accettare.")


def _apply_discount_filter(driver, min_discount_label: str | int | None):
    if not min_discount_label:
        return
    try:
        filter_button = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.XPATH, f'//input[@name="percentOff" and @value="{min_discount_label}"]'))
        )
        driver.execute_script("arguments[0].click();", filter_button)
        time.sleep(3)
        logger.info(f"✅ Filtro sconto Amazon {min_discount_label}% applicato.")
    except Exception as e:
        logger.info(f"ℹ️ Filtro sconto Amazon non disponibile su questa pagina: {e}")


def _extract_asins_from_page(driver) -> set[str]:
    asins: set[str] = set()
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/dp/'], a[href*='/gp/product/'], a[href*='/product/']")
        for el in elements:
            href = el.get_attribute("href") or ""
            for match in _ASIN_RE.finditer(href):
                asins.add(match.group(1).upper())
    except Exception as e:
        logger.warning(f"⚠️ Errore lettura ASIN dalla pagina: {e}")
    return asins


def _scroll_and_scan(driver, asins: set[str], max_scrolls: int, page_index: int):
    before_page = len(asins)
    asins.update(_extract_asins_from_page(driver))

    for step in range(max_scrolls):
        try:
            driver.execute_script(f"window.scrollBy(0, {SCROLL_AMOUNT_PX});")
        except WebDriverException:
            break
        logger.info(f"🔃 Pagina {page_index} scroll {step + 1}/{max_scrolls}")
        time.sleep(SCROLL_DELAY)

        if _is_block_page(driver):
            logger.warning("⚠️ CAPTCHA / block page rilevata durante lo scroll.")
            break

        asins.update(_extract_asins_from_page(driver))

    new_count = len(asins) - before_page
    logger.info(f"📦 Pagina {page_index}: nuovi ASIN={new_count} | totale={len(asins)}")


def _find_next_url_or_button(driver):
    # Amazon search pages normally expose rel/label based next links.
    selectors = [
        "a.s-pagination-next:not(.s-pagination-disabled)",
        "a[aria-label*='Vai alla pagina successiva']",
        "a[aria-label*='pagina successiva']",
        "a[aria-label*='Next']",
        "li.a-last a",
    ]
    for css in selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, css)
            for el in elems:
                if not el.is_displayed():
                    continue
                classes = (el.get_attribute("class") or "").lower()
                aria_disabled = (el.get_attribute("aria-disabled") or "").lower()
                if "disabled" in classes or aria_disabled == "true":
                    continue
                href = el.get_attribute("href")
                if href:
                    return urljoin(driver.current_url, href), None
                return None, el
        except Exception:
            continue
    return None, None


def _go_next_page(driver) -> bool:
    next_url, next_button = _find_next_url_or_button(driver)
    if not next_url and not next_button:
        logger.info("⛔ Nessun pulsante/link pagina successiva trovato.")
        return False

    try:
        if next_url:
            logger.info(f"➡️ Apro pagina successiva: {next_url}")
            driver.get(next_url)
        else:
            logger.info("➡️ Click su pagina successiva")
            driver.execute_script("arguments[0].click();", next_button)
        time.sleep(4)
        return not _is_block_page(driver)
    except Exception as e:
        logger.info(f"⛔ Impossibile passare alla pagina successiva: {e}")
        return False


def collect_asins_from_category(
    *,
    url: str,
    label: str,
    min_discount_label: str | int = "3",
    max_scrolls: int | None = None,
    max_pages: int | None = None,
) -> list[str]:
    """
    Scanner unico per categorie Amazon.

    Prima legge la pagina corrente tramite scroll, poi prova a passare alla pagina successiva.
    Questo evita il limite delle categorie che caricano pochi prodotti solo con lo scroll.
    """
    max_scrolls = int(max_scrolls or MAX_SCROLLS)
    max_pages = int(max_pages or DEFAULT_CATEGORY_MAX_PAGES)
    max_pages = max(1, min(max_pages, 10))

    driver = None
    asins: set[str] = set()

    try:
        driver = start_selenium()
        logger.info(f"🌐 Apro categoria {label}: {url}")
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning(f"⏳ Timeout caricamento categoria {label}, provo comunque a leggere la pagina.")

        time.sleep(3)
        if _is_block_page(driver):
            logger.warning(f"⚠️ CAPTCHA / block page su categoria {label}.")
            return []

        _accept_cookies(driver)
        _apply_discount_filter(driver, min_discount_label)

        page = 1
        visited_urls: set[str] = set()
        while page <= max_pages:
            current_url = driver.current_url or ""
            if current_url in visited_urls:
                logger.info("⛔ Pagina già visitata, interrompo paginazione per evitare loop.")
                break
            visited_urls.add(current_url)

            logger.info(f"📄 Categoria {label}: scansione pagina {page}/{max_pages}")
            _scroll_and_scan(driver, asins, max_scrolls, page)

            if page >= max_pages:
                break

            if not _go_next_page(driver):
                break
            page += 1

        logger.info(f"✅ Totale ASIN trovati ({label}): {len(asins)} | pagine={page} | scroll={max_scrolls}")
        return sorted(asins)

    except Exception as e:
        logger.exception(f"❌ Errore scanner categoria {label}: {e}")
        return []

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
