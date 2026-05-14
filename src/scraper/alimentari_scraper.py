import os
import re
import time
import logging

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from urllib3.exceptions import MaxRetryError

from src.configs.scraper_settings import MAX_SCROLLS, SCROLL_DELAY, SCROLL_AMOUNT_PX

logger = logging.getLogger(__name__)


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
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    if os.environ.get("REMOTE_CHROMIUM"):
        try:
            driver = webdriver.Remote(
                command_executor=os.environ["REMOTE_CHROMIUM"],
                options=options
            )
        except MaxRetryError:
            driver = webdriver.Remote(
                command_executor=os.environ["REMOTE_CHROMIUM"] + "/wd/hub",
                options=options
            )

        driver.command_executor._conn.timeout = 300
    else:
        driver = webdriver.Chrome(service=chromium_service, options=options)

    driver.set_page_load_timeout(180)
    driver.implicitly_wait(10)
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
    try:
        cookie_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "sp-cc-accept"))
        )
        cookie_button.click()
        time.sleep(1)
        logger.info("✅ Cookie accettati.")
    except Exception:
        logger.info("ℹ️ Nessun cookie da accettare.")


def _extract_asins_from_page(driver) -> set[str]:
    asins = set()

    try:
        deal_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']")
        for el in deal_elements:
            href = el.get_attribute("href")
            if not href:
                continue

            match = re.search(r"/dp/([A-Z0-9]{10})", href, re.IGNORECASE)
            if match:
                asins.add(match.group(1).upper())
    except Exception as e:
        logger.warning(f"⚠️ Errore scansione elementi ASIN: {e}")

    return asins


def get_asins_from_alimentari(min_discount_label="3", max_scrolls=5):
    """
    Recupera ASIN da una ricerca generica Amazon con eventuale filtro sconto.
    """
    url = "https://www.amazon.it/alimentari/s?k=alimentari"
    driver = start_selenium()
    asins = set()

    max_scrolls = max_scrolls or MAX_SCROLLS

    try:
        logger.info(f"🌐 Apro URL: {url}")
        driver.get(url)
        time.sleep(3)

        if _is_block_page(driver):
            logger.warning("⚠️ CAPTCHA / block page rilevata all'apertura pagina.")
            return []

        _accept_cookies(driver)

        if _is_block_page(driver):
            logger.warning("⚠️ CAPTCHA / block page rilevata dopo gestione cookie.")
            return []

        # Tentativo filtro sconto
        try:
            filter_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, f'//input[@name="percentOff" and @value="{min_discount_label}"]')
                )
            )
            driver.execute_script("arguments[0].click();", filter_button)
            time.sleep(3)
            logger.info(f"✅ Filtro sconto {min_discount_label}% applicato.")
        except Exception as e:
            logger.warning(f"⚠️ Filtro sconto non cliccabile o assente: {e}")

        if _is_block_page(driver):
            logger.warning("⚠️ CAPTCHA / block page rilevata dopo filtro.")
            return []

        logger.info("🔍 Scansione iniziale...")
        asins.update(_extract_asins_from_page(driver))

        for step in range(max_scrolls):
            driver.execute_script(f"window.scrollBy(0, {SCROLL_AMOUNT_PX});")
            logger.info(f"🔃 Scroll {step + 1}/{max_scrolls}")
            time.sleep(SCROLL_DELAY)

            if _is_block_page(driver):
                logger.warning("⚠️ CAPTCHA / block page rilevata durante lo scroll.")
                break

            new_asins = _extract_asins_from_page(driver)
            asins.update(new_asins)

        logger.info(f"✅ Totale ASIN trovati (all): {len(asins)}")
        return list(asins)

    except TimeoutException:
        logger.error("❌ Timeout caricamento pagina Amazon.")
        return []

    except Exception as e:
        logger.exception(f"❌ Errore generale in get_asins_from_all: {e}")
        return []

    finally:
        try:
            driver.quit()
        except Exception:
            pass
