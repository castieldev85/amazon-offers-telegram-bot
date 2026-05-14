import json
import os
import time
import random
import logging
from typing import Optional

from paapi5_python_sdk.api.default_api import DefaultApi
from paapi5_python_sdk.get_items_request import GetItemsRequest
from paapi5_python_sdk.get_items_resource import GetItemsResource
from paapi5_python_sdk.search_items_request import SearchItemsRequest
from paapi5_python_sdk.search_items_resource import SearchItemsResource
from paapi5_python_sdk.partner_type import PartnerType
from paapi5_python_sdk.rest import ApiException

from src.utils.product import Product

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
ACCESS_KEY = os.getenv("AMAZON_PAAPI_ACCESS_KEY", "").strip()
SECRET_KEY = os.getenv("AMAZON_PAAPI_SECRET_KEY", "").strip()
PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "tuotag-21").strip()
REGION = os.getenv("AMAZON_PAAPI_REGION", "eu-west-1").strip()
HOST = os.getenv("AMAZON_PAAPI_HOST", "webservices.amazon.it").strip()
MARKETPLACE = os.getenv("AMAZON_PAAPI_MARKETPLACE", "www.amazon.it").strip()

MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.5


# -----------------------------------------------------------------------------
# INTERNAL HELPERS
# -----------------------------------------------------------------------------
def _credentials_are_configured() -> bool:
    return bool(ACCESS_KEY and SECRET_KEY and PARTNER_TAG and REGION and HOST)


def _build_api_client() -> DefaultApi:
    if not _credentials_are_configured():
        raise RuntimeError(
            "Credenziali PA-API mancanti. "
            "Configura AMAZON_PAAPI_ACCESS_KEY, AMAZON_PAAPI_SECRET_KEY, AMAZON_PARTNER_TAG, "
            "AMAZON_PAAPI_REGION, AMAZON_PAAPI_HOST."
        )

    return DefaultApi(
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        host=HOST,
        region=REGION
    )


def _parse_api_error(e: ApiException) -> tuple[str, str]:
    try:
        error_body = json.loads(e.body)
        error_info = error_body.get("Errors", [{}])[0]
        return (
            error_info.get("Code", f"HTTP_{getattr(e, 'status', 'UNKNOWN')}"),
            error_info.get("Message", str(e)),
        )
    except Exception:
        return (f"HTTP_{getattr(e, 'status', 'UNKNOWN')}", str(e))


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            return float(value)

        s = str(value).strip()
        if not s:
            return None

        s = s.replace("€", "").replace(",", ".").strip()
        return float(s)
    except Exception:
        return None


def _extract_primary_listing(item):
    try:
        if item and item.offers and item.offers.listings:
            return item.offers.listings[0]
    except Exception:
        pass
    return None


def _extract_title(item) -> str:
    try:
        if item.item_info and item.item_info.title and item.item_info.title.display_value:
            return item.item_info.title.display_value.strip()
    except Exception:
        pass
    return "N/D"


def _extract_image(item) -> str:
    try:
        if (
            item.images
            and item.images.primary
            and item.images.primary.large
            and item.images.primary.large.url
        ):
            return item.images.primary.large.url
    except Exception:
        pass
    return ""


def _extract_category(item) -> str:
    try:
        if item.browse_node_info and item.browse_node_info.browse_nodes:
            node = item.browse_node_info.browse_nodes[0]
            if node and node.display_name:
                return node.display_name
    except Exception:
        pass
    return "Elettronica"


def _extract_prices_from_listing(listing) -> tuple[str, str, int, bool]:
    price_str = "0.0"
    old_price_str = "0.0"
    discount = 0
    has_coupon = False

    if not listing:
        return price_str, old_price_str, discount, has_coupon

    try:
        if getattr(listing, "price", None) and getattr(listing.price, "amount", None) is not None:
            price_str = str(listing.price.amount)

        if getattr(listing, "saving_basis", None) and getattr(listing.saving_basis, "amount", None) is not None:
            old_price_str = str(listing.saving_basis.amount)

        if getattr(listing, "promotions", None):
            has_coupon = True

        price_val = _safe_float(price_str)
        old_price_val = _safe_float(old_price_str)

        if price_val is not None and old_price_val is not None and old_price_val > price_val and old_price_val > 0:
            discount = int(round((old_price_val - price_val) / old_price_val * 100))

    except Exception:
        logger.exception("[PAAPI] Errore durante il parsing dei prezzi")

    return price_str, old_price_str, discount, has_coupon


def _build_affiliate_link(asin: str, partner_tag: Optional[str] = None) -> str:
    tag = (partner_tag or PARTNER_TAG).strip()
    if tag:
        return f"https://www.amazon.it/dp/{asin}?tag={tag}"
    return f"https://www.amazon.it/dp/{asin}"


# -----------------------------------------------------------------------------
# PUBLIC API
# -----------------------------------------------------------------------------
def fetch_product_details_from_api(asin: str, partner_tag: Optional[str] = None) -> Product | None:
    """
    Recupera i dettagli di un prodotto da PA-API partendo dall'ASIN.
    Compatibile con il tuo main.py attuale.
    """
    if not asin:
        logger.warning("[PAAPI][GET_ITEMS] ASIN vuoto")
        return None

    try:
        api = _build_api_client()
    except Exception as e:
        logger.error(f"[PAAPI][GET_ITEMS] Configurazione API non valida: {e}")
        return None

    request = GetItemsRequest(
        partner_tag=PARTNER_TAG,
        partner_type=PartnerType.ASSOCIATES,
        marketplace=MARKETPLACE,
        item_ids=[asin],
        resources=[
            GetItemsResource.ITEMINFO_TITLE,
            GetItemsResource.OFFERS_LISTINGS_PRICE,
            GetItemsResource.OFFERS_LISTINGS_SAVINGBASIS,
            GetItemsResource.OFFERS_LISTINGS_PROMOTIONS,
            GetItemsResource.IMAGES_PRIMARY_LARGE,
            GetItemsResource.BROWSENODEINFO_BROWSENODES,
        ],
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"[PAAPI][GET_ITEMS] ASIN={asin} tentativo={attempt}")
            response = api.get_items(request)

            if getattr(response, "errors", None):
                for err in response.errors:
                    logger.error(
                        f"[PAAPI][GET_ITEMS] Errore logico ASIN={asin} "
                        f"code={getattr(err, 'code', 'UNKNOWN')} "
                        f"message={getattr(err, 'message', 'N/A')}"
                    )
                return None

            if not getattr(response, "items_result", None) or not getattr(response.items_result, "items", None):
                logger.warning(f"[PAAPI][GET_ITEMS] Nessun item per ASIN={asin}")
                return None

            item = response.items_result.items[0]
            listing = _extract_primary_listing(item)

            if listing is None:
                logger.warning(
                    f"[PAAPI][GET_ITEMS] Offers/Listings assenti per ASIN={asin}. "
                    "Possibile prodotto senza offerta compatibile o account API limitato."
                )

            price, old_price, discount, has_coupon = _extract_prices_from_listing(listing)
            title = _extract_title(item)
            image = _extract_image(item)
            category = _extract_category(item)

            product = Product(
                asin=asin,
                title=title,
                price=price,
                old_price=old_price,
                discount=discount,
                image=image,
                has_coupon=has_coupon,
                is_limited_offer=False,
                category=category,
                link=_build_affiliate_link(asin, partner_tag),
            )

            logger.info(
                f"[PAAPI][GET_ITEMS] OK ASIN={asin} "
                f"title={title[:60]!r} price={price} old_price={old_price} discount={discount}"
            )
            return product

        except ApiException as e:
            code, msg = _parse_api_error(e)

            if getattr(e, "status", None) == 429:
                wait = BASE_RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0.2, 0.8)
                logger.warning(
                    f"[PAAPI][GET_ITEMS] 429 TooManyRequests ASIN={asin} "
                    f"tentativo={attempt}/{MAX_RETRIES} attesa={wait:.1f}s msg={msg}"
                )
                time.sleep(wait)
                continue

            logger.error(
                f"[PAAPI][GET_ITEMS] ApiException ASIN={asin} "
                f"status={getattr(e, 'status', 'N/A')} code={code} msg={msg}"
            )
            break

        except Exception as e:
            logger.exception(f"[PAAPI][GET_ITEMS] Errore imprevisto ASIN={asin}: {e}")
            break

    return None


def search_items_with_retry(keywords: str, item_count: int = 10, page: int = 1):
    """
    Esegue SearchItems con retry minimo e logging uniforme.
    Restituisce la response SDK oppure None.
    """
    if not keywords or not keywords.strip():
        logger.warning("[PAAPI][SEARCH] keywords vuote")
        return None

    try:
        api = _build_api_client()
    except Exception as e:
        logger.error(f"[PAAPI][SEARCH] Configurazione API non valida: {e}")
        return None

    request = SearchItemsRequest(
        partner_tag=PARTNER_TAG,
        partner_type=PartnerType.ASSOCIATES,
        marketplace=MARKETPLACE,
        keywords=keywords.strip(),
        search_index="All",
        item_count=item_count,
        item_page=page,
        resources=[
            SearchItemsResource.ITEMINFO_TITLE,
            SearchItemsResource.OFFERS_LISTINGS_PRICE,
            SearchItemsResource.OFFERS_LISTINGS_SAVINGBASIS,
            SearchItemsResource.IMAGES_PRIMARY_LARGE,
        ],
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"[PAAPI][SEARCH] keywords={keywords!r} page={page} "
                f"item_count={item_count} tentativo={attempt}"
            )

            response = api.search_items(request)

            if getattr(response, "errors", None):
                for err in response.errors:
                    logger.error(
                        f"[PAAPI][SEARCH] Errore logico "
                        f"code={getattr(err, 'code', 'UNKNOWN')} "
                        f"message={getattr(err, 'message', 'N/A')}"
                    )
                return None

            return response

        except ApiException as e:
            code, msg = _parse_api_error(e)

            if getattr(e, "status", None) == 429:
                wait = BASE_RETRY_DELAY * (2 ** (attempt - 1)) + random.uniform(0.2, 0.8)
                logger.warning(
                    f"[PAAPI][SEARCH] 429 TooManyRequests keywords={keywords!r} "
                    f"tentativo={attempt}/{MAX_RETRIES} attesa={wait:.1f}s msg={msg}"
                )
                time.sleep(wait)
                continue

            logger.error(
                f"[PAAPI][SEARCH] ApiException keywords={keywords!r} "
                f"status={getattr(e, 'status', 'N/A')} code={code} msg={msg}"
            )
            break

        except Exception as e:
            logger.exception(f"[PAAPI][SEARCH] Errore imprevisto keywords={keywords!r}: {e}")
            break

    return None


def search_products_by_keyword(
    keywords: str,
    min_discount: int = 10,
    max_price: float | None = None,
    item_count: int = 10,
    page: int = 1
) -> list[str]:
    """
    Cerca prodotti per keyword e restituisce gli ASIN che rispettano i filtri.
    """
    response = search_items_with_retry(
        keywords=keywords,
        item_count=item_count,
        page=page
    )

    if not response or not getattr(response, "search_result", None) or not getattr(response.search_result, "items", None):
        logger.warning(f"[PAAPI][SEARCH_FILTER] Nessun risultato per keywords={keywords!r}")
        return []

    valid_asins: list[str] = []

    for item in response.search_result.items:
        try:
            asin = getattr(item, "asin", None)
            if not asin:
                continue

            listing = _extract_primary_listing(item)
            price_str, old_price_str, discount, _ = _extract_prices_from_listing(listing)

            price_val = _safe_float(price_str)
            old_price_val = _safe_float(old_price_str)

            passes_discount = discount >= min_discount
            passes_price = max_price is not None and price_val is not None and price_val <= max_price

            # Se max_price è None, il filtro prezzo non deve contare
            if passes_discount or passes_price:
                valid_asins.append(asin)

            logger.debug(
                f"[PAAPI][SEARCH_FILTER] asin={asin} price={price_val} old_price={old_price_val} "
                f"discount={discount} min_discount={min_discount} max_price={max_price}"
            )

        except Exception:
            logger.exception("[PAAPI][SEARCH_FILTER] Errore parsing item SearchItems")
            continue

    logger.info(
        f"[PAAPI][SEARCH_FILTER] keywords={keywords!r} valid_asins={len(valid_asins)} "
        f"min_discount={min_discount} max_price={max_price}"
    )
    return valid_asins
