import os
import re
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib3.exceptions import MaxRetryError
from selenium.common.exceptions import TimeoutException
from src.configs.scraper_settings import MAX_SCROLLS, SCROLL_DELAY, SCROLL_AMOUNT_PX

logger = logging.getLogger(__name__)

def start_selenium():
    chromium_service = Service()
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=1")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--enable-unsafe-swiftshader")

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
        # Increase HTTP timeout to 300s
        driver.command_executor._conn.timeout = 300
    else:
        driver = webdriver.Chrome(service=chromium_service, options=options)

    # Selenium timeouts
    driver.set_page_load_timeout(180)    # max 3 minutes for page load
    driver.implicitly_wait(10)           # implicit wait up to 10s
    return driver

def get_offerte_giorno_asins(min_discount_label="3", max_scrolls=5):
    url = "https://www.amazon.it/gp/goldbox"
    driver = start_selenium()
    asins = set()

    try:
        driver.get(url)
        time.sleep(3)

        try:
            cookie_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "sp-cc-accept"))
            )
            cookie_button.click()
            time.sleep(1)
            logger.info("✅ Cookie accettati.")
        except:
            logger.info("ℹ️ Nessun cookie da accettare.")

        try:
            filter_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, f'//input[@name="percentOff" and @value="{min_discount_label}"]'))
            )
            driver.execute_script("arguments[0].click();", filter_button)
            time.sleep(3)
            logger.info(f"✅ Filtro sconto {min_discount_label}% applicato.")
        except Exception as e:
            logger.warning(f"⚠️ Filtro sconto non cliccabile o assente: {e}")

        def scan():
            deal_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']")
            for el in deal_elements:
                href = el.get_attribute("href")
                match = re.search(r"/dp/(\w{10})", href)
                if match:
                    asins.add(match.group(1))

        logger.info("🔍 Scansione iniziale...")
        scan()

        for step in range(MAX_SCROLLS):
            driver.execute_script(f"window.scrollBy(0, {SCROLL_AMOUNT_PX});")
            logger.info(f"🔃 Scroll {step + 1}")
            time.sleep(SCROLL_DELAY)
            scan()

        logger.info(f"✅ Totale ASIN trovati (alimentari): {len(asins)}")

    finally:
        driver.quit()

    return list(asins)

