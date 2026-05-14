# CHANGELOG V2.8 - Buffer auto-ripulente

- Se l'autopost trova prodotti nel buffer ma nessun candidato supera lo score minimo, elimina il buffer della categoria.
- Dopo la pulizia avvia subito un nuovo refill/scansione per quella categoria.
- Gli ASIN scartati per score basso vengono messi in quarantena temporanea in `rejected_offers.json`.
- Il refill salta gli ASIN in quarantena, così non ricarica sempre gli stessi prodotti sporchi nel buffer.
- Nuove variabili `.env`: `INVALID_BUFFER_QUARANTINE_HOURS` e `REJECTED_OFFERS_PATH`.

# CHANGELOG V2.4

- Aggiunto filtro qualità severo sui prezzi precedenti.
- Il campo `Prima:` viene mostrato solo se il prezzo vecchio è credibile.
- Bloccati sconti assurdi o rumorosi oltre il 70% quando non sono verificabili.
- Bloccati rapporti prezzo vecchio/prezzo attuale troppo alti, ad esempio 145,99€ -> 19,99€.
- Rimossi codici promo generici/spazzatura come `ARTICOLO`, `SCONTO`, `PROMO`, `CODICE`.
- Coupon mostrati solo se contengono un valore reale in euro o percentuale.
- Immagine offerta aggiornata: mostra vecchio prezzo e badge sconto solo quando il dato è affidabile.
- Aggiornato anche lo scoring per non premiare vecchi prezzi o promo code non validi.

# CHANGELOG V2.3

- Rimossa dalla grafica la frase: "Prezzo e disponibilità da verificare su Amazon".
- Immagine offerta ancora più pulita: prodotto, badge, prezzo, eventuale vecchio prezzo/sconto e brand.

# CHANGELOG V2

## Fix critici

- Corretto errore di sintassi in `src/scraper/search_scraper.py`.
- Corretto errore di sintassi in `src/tracking/price_tracker.py`.
- Corretto anti-duplicato offerte in `src/utils/database_builder.py`.
- Rimosse chiavi Amazon hardcoded da `src/utils/radar_price_error_detector.py`.
- Protetto `/attivalicenza` con `ADMIN_IDS`.
- Aggiunto controllo iniziale se manca `TELEGRAM_BOT_TOKEN`.

## Pulizia progetto

- Rimossi file `*_old.py` non usati.
- Rimossi cache, log, buffer già generati e risultati ricerca personali.
- Aggiunti `.env.example`, `requirements.txt`, `README_AVVIO_V2.md`.
- Aggiunti script PowerShell `installa_dipendenze.ps1` e `avvia_bot.ps1`.

## Migliorie pubblicazione

- Refill e pubblicazione separati.
- Lo scheduler V2 sceglie le offerte migliori tra tutte le categorie.
- Pubblica massimo `MAX_OFFERS_PER_USER_CYCLE` offerte per ciclo utente.
- Aggiunto filtro qualità `MIN_OFFER_SCORE`.
- Messaggio offerta più pulito e titolo Amazon accorciato.
- Buffer deduplicato per ASIN.

## Comandi admin

- `/adminstats` mostra utenti, categorie attive, prodotti nei buffer e link generati.
- `/attivalicenza <user_id> <giorni>` ora è riservato agli admin.


## V2.2 - Post Telegram più puliti

- Nuova immagine offerta più minimale e moderna.
- Rimossi valori sporchi tipo `None€`.
- Sconti estremi tipo `-97%` mostrati solo se il prezzo vecchio è reale e coerente.
- Caption Telegram più corta e leggibile.
- Pulsante CTA rinominato in `Apri offerta Amazon`.
- Brand della grafica configurabile con `BOT_BRAND_TEXT` in `.env`.


## V2.5 - categorie corrette

- Rinominata categoria `cat_goldbox` da "Settimana Black Friday" a "Offerte del giorno".
- Rinominata `cat_deals` da "Offerte Prime" a "Offerte Amazon" perché lo scraper usa la pagina generale offerte.
- Aggiunto `cat_goldbox` anche nella mappa autoposting, così funziona anche nel ciclo automatico e non solo nel refill manuale.
- Allineato `scraper_category_urls.py` con tutte le categorie presenti nel menu.
- Aggiunto bonus scoring per `cat_goldbox`.


## V2.6 - Numero offerte configurabile

- Aggiunto in Impostazioni > Tempi il settaggio "Numero offerte per ciclo".
- Ogni utente può scegliere quante offerte pubblicare a ogni ciclo automatico: 1, 2, 3, 4, 5, 6, 8 o 10.
- Il valore viene salvato in `user_data.json` come `offers_per_cycle`.
- L’autoposting usa il valore scelto dall’utente invece del limite fisso globale.


## V2.7 - Offerte per categoria

- Il settaggio "Numero offerte per ciclo" ora viene applicato per ogni categoria attiva.
- Esempio: valore 2 + 2 categorie attive = fino a 4 post totali per ciclo, cioè 2 offerte per categoria.
- L’autoposting seleziona le migliori offerte dentro ogni singola categoria, non più solo le migliori globali.
- Rimangono attivi score minimo, anti-duplicato, buffer e controllo orario.


## V2.9 - Fix fuori orario

- Il pacchetto non contiene più una fascia oraria precompilata per l'admin: `user_schedule_config.json` parte vuoto.
- Se il bot è fuori orario, non resta più in loop a loggare ogni minuto: calcola il prossimo orario utile e sposta `next_post`.
- Se imposti lo stesso orario di inizio e fine, la fascia viene interpretata come 24 ore attive.
- Il refill continua a lavorare anche fuori orario, ma la pubblicazione aspetta la fascia configurata.


## V2.10 - Fix lock Windows user_data.json

- Corretto errore `PermissionError WinError 32` su Windows durante il refill.
- `user_data.json` ora usa un lock interno thread-safe.
- I file temporanei JSON ora sono unici per processo/thread, quindi più refill paralleli non si pestano i piedi.
- I getter non riscrivono più `user_data.json` a ogni lettura: salvano solo quando mancano campi default.
