# Scroll settings
MAX_SCROLLS = 12
SCROLL_DELAY = 8  # secondi tra uno scroll e l'altro
SCROLL_AMOUNT_PX = 500

# Filtro minimo per lo sconto da applicare nei bottoni
DEFAULT_MIN_DISCOUNT_LABEL = "3"
DEFAULT_MIN_DISCOUNT_PERCENT = 20


# Paginazione categoria: oltre allo scroll, il bot può aprire le pagine successive.
DEFAULT_CATEGORY_MAX_PAGES = int(__import__("os").getenv("DEFAULT_CATEGORY_MAX_PAGES", "2"))
