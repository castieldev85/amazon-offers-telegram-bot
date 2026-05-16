# V3.26 - Validazione prezzi live Fonti Telegram

- Le offerte importate da fonti Telegram vengono ricontrollate prima della pubblicazione.
- Il prezzo live Amazon vince sempre sul prezzo letto dal post sorgente.
- Se Amazon/HTML/Selenium non conferma il prezzo, l'offerta viene scartata di default.
- Aggiunte variabili `.env`: `TELEGRAM_SOURCE_ALLOW_UNVERIFIED_PRICE` e `TELEGRAM_SOURCE_PRICE_MAX_DIFF_PERCENT`.
- Evita mismatch tipo fonte Telegram a 15,00€ ma pagina Amazon a 34,68€.

# V3.22 - Fix upload foto Facebook

- Preparazione JPEG standard prima dell'upload Facebook.
- Upload Graph API con filename e content-type `image/jpeg`.
- Pubblicazione forzata come foto con `published=true`.
- Log dimensione/validità immagine prima dell'invio.


## V3.20 - Fix refill manuale Fonti Telegram

- Corretto errore `KeyError: cat_telegram_sources` quando si puliva/refillava il buffer Fonti Telegram dalla dashboard.
- La categoria `cat_telegram_sources` ora usa correttamente le fonti salvate e `import_channel_offers_to_buffer`, non la mappa degli scraper Amazon tradizionali.
- Aggiunto log riepilogativo con fonti scansionate, link, ASIN e prodotti aggiunti.

# V3.6 - ASIN Detail Scraper Upgrade

- Migliorata pipeline dettaglio ASIN: PA-API → HTML unico → Selenium fallback.
- Ridotti prodotti a prezzo 0 nel buffer.
- Aggiunto recupero prezzo da JSON-LD e meta tag Amazon.
- Migliorato recupero immagine prodotto da data-a-dynamic-image.
- Selenium ora legge anche textContent/aria-label per prezzi nascosti in .a-offscreen.
- Aggiunte impostazioni .env per fallback Selenium e limite Chrome paralleli.

# V3.4 - Notifica utente licenza attivata

- Quando l’admin attiva una licenza dal pannello, l’utente riceve un messaggio privato di conferma.
- Il messaggio indica durata della licenza e data di scadenza.
- Anche il comando `/attivalicenza <user_id> <giorni>` invia la notifica all’utente.
- Nel pannello admin viene mostrato se la notifica è stata inviata oppure no.

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

## V3.1 Premium Dashboard

- Ridisegnata la dashboard principale in un unico messaggio Telegram.
- Aggiunta panoramica con categorie attive, buffer, canali, licenza e configurazione.
- Aggiunta sezione Buffer con stato per categoria.
- Riorganizzati i pulsanti principali in stile control panel.
- Migliorati testi delle sezioni Categorie, Impostazioni, Funzioni, Info e Canali.
- Mantenuto il comportamento a messaggio singolo tramite edit_message_text.


## V3.2 - Fix filtro sconto obbligatorio

- Il filtro sconto impostato dall’utente ora è obbligatorio anche in autopost.
- Un’offerta con score alto non può più bypassare il filtro sconto minimo.
- Il refill salva nel buffer solo prodotti che rispettano sia sconto minimo sia score minimo.
- Aggiunto calcolo `sconto_effettivo` basato su prezzo vecchio affidabile, coupon e promo reali.
- Rimossa la duplicazione “Prima -X%” + “Risparmio stimato -X%” quando il valore è identico.

## V3.3 - Pannello admin licenze

- Aggiunta gestione licenze direttamente dal pannello Telegram.
- Nuovo pulsante admin: `👑 Gestisci Licenze` dentro `🎟️ Licenza & Affiliazione`.
- Attivazione guidata licenze a 30, 90, 180, 365 giorni.
- Durata licenza manuale personalizzata.
- Lista utenti registrati con stato licenza e scadenza.
- Inserimento Telegram ID guidato senza usare per forza `/attivalicenza`.
- Comando `/attivalicenza <user_id> <giorni>` mantenuto e ancora protetto admin.

## V3.5 - Scroll categorie da dashboard

- Aggiunta sezione `🔃 Scroll categorie` dentro `⚙️ Impostazioni`.
- Aggiunta guida interna `📘 Guida scroll` per spiegare quando usare pochi o tanti scroll.
- Aggiunto salvataggio per utente di `category_scrolls` in `user_data.json`.
- Allineati gli scraper categoria: ora ricevono `max_scrolls` dal pannello invece di usare valori diversi nei singoli file.
- Il refill passa il valore scroll dell'utente alla funzione scraper della categoria.
- Quando cambi il numero di scroll, i buffer delle categorie attive vengono cancellati per forzare un nuovo refill coerente con la nuova impostazione.


## V3.8 - Fonti Telegram da dashboard

- Aggiunta sezione `📥 Fonti Telegram` dentro `🛠️ Funzioni`.
- Possibilità di inserire il nome di un canale Telegram pubblico come fonte offerte.
- Scansione tramite preview pubblica `t.me/s/<canale>` senza richiedere DNS/server esterni.
- Estrazione link Amazon dai post recenti.
- Recupero ASIN e dati prodotto con la pipeline scraper del bot.
- Salvataggio offerte valide nel buffer `Fonti Telegram 📥`.
- Attivazione automatica della categoria `Fonti Telegram 📥` quando si aggiunge una fonte.
- Aggiunta guida interna della funzione nella dashboard.


## V3.9 - Fix ASIN da fonti Telegram

- Migliorata estrazione ASIN da canali Telegram pubblici.
- Supporto link Amazon senza protocollo, link codificati, bottoni inline e short-link amzn.to/amzn.eu/a.co.
- Aggiunto parser ASIN diretto dal testo del messaggio.
- Aggiunti log dettagliati per link trovati, ASIN trovati, prodotti aggiunti e scartati.

## V3.10 - Fix Fonti Telegram persistenti e diagnostica bot/canali

- Le fonti Telegram vengono salvate anche in `telegram_sources.json`, quindi restano dopo il riavvio.
- Migrazione automatica delle vecchie fonti da `user_data.json`.
- Migliorata la diagnostica quando si inserisce un bot Telegram invece di un canale pubblico.
- La scansione ora segnala chiaramente quando `t.me/s/<nome>` non espone messaggi pubblici.
- Migliorata l'estrazione di link Amazon/ASIN da HTML, href, redirect e URL codificati.


## V3.12 - Fix pubblicazione offerte senza immagine

- Corretto crash `Invalid URL 'None'` quando una fonte Telegram fornisce prezzo ma non immagine.
- `image_builder.py` ora genera un placeholder pulito se `image_url` manca, è `None` o non è scaricabile.
- `autoposting.py` ora può pubblicare anche solo testo se la generazione immagine fallisce.
- Le offerte importate da Telegram con prezzo valido non vengono più bloccate solo perché manca l'immagine.

## V3.13 - Fonti Telegram: immagini prodotto + refill automatico

- Aggiunto recupero immagine direttamente dalla preview pubblica Telegram (`t.me/s/...`) quando Amazon/PA-API non restituisce la foto prodotto.
- Le offerte importate da fonti Telegram usano l'immagine del post come fallback, evitando placeholder quando possibile.
- Quando il buffer `Fonti Telegram 📥` scende sotto il minimo, il refill loop riscansiona automaticamente i canali salvati.
- Le fonti Telegram salvate in `telegram_sources.json` vengono riutilizzate senza doverle reinserire dopo il riavvio.


## V3.17 - Ottimizzazione Telethon media

- Corretto bug Telethon con funzioni interne mancanti per titolo/link ASIN.
- Ottimizzato download foto: ora scarica media solo dai messaggi che contengono link/ASIN Amazon.
- Aggiunta variabile `TELETHON_MAX_MEDIA_DOWNLOADS` per limitare il numero di immagini scaricate per scansione.
- Ridotti log ripetitivi `Starting direct file download`.


## V3.18 - Dashboard limite Fonti Telegram

- Aggiunta sezione `Fonti Telegram → Limite lettura`.
- Limite configurabile da dashboard: 10, 20, 30, 50, 75, 100, 150, 200 messaggi.
- Aggiunta guida interna che spiega perché leggere 30 messaggi non significa importare 30 offerte.
- Aumentato limite massimo salvabile da 100 a 200 messaggi.


## V3.19 - Fix foto fonti Telegram persistenti

- Le immagini scaricate da Telethon vengono ora salvate in una cartella persistente.
- I prodotti importati da fonti Telegram non perdono più la foto prima dell'autopost.
- Aggiunto fallback per immagini locali/relative e controllo robusto su `product.image`.
- Aggiunto log `image=yes/no` quando un prodotto entra nel buffer fonti Telegram.
- Nuova variabile `.env`: `TELEGRAM_SOURCE_MEDIA_PATH=telegram_source_media`.

## V3.21 - Pulizia immagini fonti Telegram

- Le immagini importate da fonti Telegram vengono pulite prima di essere salvate.
- Sulle grafiche larghe/composite viene tenuta solo la zona prodotto a sinistra.
- Rimossi automaticamente pannelli prezzo, badge e watermark del canale sorgente quando presenti nella parte destra/bassa dell'immagine.
- Aggiunte variabili `.env`:
  - `TELEGRAM_SOURCE_CLEAN_MEDIA=true`
  - `TELEGRAM_SOURCE_TRIM_BOTTOM_WATERMARK=true`

## V3.25 - Fix offerte senza foto / placeholder

- Aggiunto recupero immagine prodotto più robusto da pagina Amazon tramite ASIN.
- Aggiunta validazione immagini per evitare placeholder bianchi o immagini quasi vuote.
- Se `REQUIRE_PRODUCT_IMAGE=true`, il bot non pubblica più offerte senza foto prodotto reale.
- Le offerte senza immagine vengono rimosse dal buffer e messe in quarantena temporanea.

## V3.30 - Paginazione categorie Amazon

- Aggiunta scansione multi-pagina per gli scraper categoria Amazon.
- Dopo gli scroll della pagina corrente, Selenium prova ad aprire la pagina successiva.
- Aggiunta impostazione dashboard: `⚙️ Impostazioni → 📄 Pagine categorie`.
- Valori disponibili: 1, 2, 3, 4, 5, 7, 10 pagine.
- Il refill passa ora sia `max_scrolls` sia `max_pages` agli scraper.
- Unificati gli scraper categoria su un runner comune per avere comportamento coerente.
