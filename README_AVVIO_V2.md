# Bot Offerte Amazon - V2 pulita

## 1) Installazione

```powershell
cd "C:\Users\Castiel\Documents\BOT_Amazon_V2"
python -m venv venv
.\venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2) Configurazione

Copia il file `.env.example` in `.env`:

```powershell
copy .env.example .env
notepad .env
```

Compila almeno:

```env
TELEGRAM_BOT_TOKEN=...
ADMIN_IDS=1271567510
AMAZON_PAAPI_ACCESS_KEY=...
AMAZON_PAAPI_SECRET_KEY=...
AMAZON_PARTNER_TAG=...
```

## 3) Avvio

```powershell
python main.py
```

Poi apri Telegram e manda:

```text
/start
```

## 4) Comandi utili

```text
/start
/track <ASIN> <prezzo>
/untrack <ASIN>
/watchlist
/radar
/adminstats
/attivalicenza <user_id> <giorni>
```

`/adminstats` e `/attivalicenza` funzionano solo per gli ID presenti in `ADMIN_IDS`.

## 5) Cosa cambia nella V2

- Rimossi file cache, log, buffer già generati e file personali.
- Aggiunto `.env.example`.
- Aggiunto `requirements.txt`.
- Fix errori di sintassi in `search_scraper.py` e `price_tracker.py`.
- Fix anti-duplicato offerte: ora usa correttamente chiave `user_id:ASIN`.
- Rimosse chiavi Amazon hardcoded dal radar offerte.
- Protetto comando `/attivalicenza` con controllo admin.
- Refill separato dalla pubblicazione: il refill riempie il buffer, lo scheduler pubblica.
- Autopost V2: sceglie la migliore offerta tra le categorie e pubblica al massimo 1 offerta per ciclo utente.
- Messaggio offerta più pulito e titolo Amazon accorciato.
- Buffer più pulito, deduplicato per ASIN e salvataggio JSON atomico.

## 6) Prima prova consigliata

1. Avvia il bot.
2. Usa `/start`.
3. Seleziona una sola categoria, ad esempio “Offerte Prime”.
4. Imposta sconto minimo non troppo alto, ad esempio 20%.
5. Aspetta il refill del buffer.
6. Controlla i log in console.

Se la PA-API non è configurata o Amazon limita le richieste, il bot proverà i fallback già presenti nel progetto.


## Personalizzare il brand sulle immagini

Nel file `.env` puoi modificare questa riga:

```env
BOT_BRAND_TEXT=t.me/amazon_offerte_sconti_coupon_top
```

Lascia vuoto il valore se non vuoi mostrare nessuna scritta sulle immagini.


## Nuova impostazione: numero offerte per ciclo

Dal menu Telegram vai in:

`⚙️ Impostazioni > ⏱️ Tempi > 🔢 Numero offerte per ciclo`

Qui puoi scegliere quante offerte il bot può pubblicare a ogni ciclo automatico **per ogni categoria attiva**. Esempio: se imposti 2 offerte, hai 2 categorie attive e l’intervallo è 30 minuti, il bot può pubblicare fino a 4 offerte ogni 30 minuti: 2 da una categoria e 2 dall’altra, rispettando sempre buffer, score minimo e anti-duplicato.


## V2.8 - Buffer auto-ripulente

Se il bot trova prodotti nel buffer ma nessuno supera `MIN_OFFER_SCORE`, ora elimina automaticamente il buffer della categoria e avvia una nuova scansione.
Gli ASIN scartati vengono salvati temporaneamente in `rejected_offers.json` per evitare che il refill ricarichi sempre gli stessi prodotti non validi.

Variabili utili nel `.env`:

```env
INVALID_BUFFER_QUARANTINE_HOURS=12
REJECTED_OFFERS_PATH=rejected_offers.json
```


## V2.9 - Fix fuori orario

- Il pacchetto non contiene più una fascia oraria precompilata per l'admin: `user_schedule_config.json` parte vuoto.
- Se il bot è fuori orario, non resta più in loop a loggare ogni minuto: calcola il prossimo orario utile e sposta `next_post`.
- Se imposti lo stesso orario di inizio e fine, la fascia viene interpretata come 24 ore attive.
- Il refill continua a lavorare anche fuori orario, ma la pubblicazione aspetta la fascia configurata.
