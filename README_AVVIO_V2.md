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

## Scroll categorie da dashboard

Nella dashboard Telegram vai su:

```text
⚙️ Impostazioni → 🔃 Scroll categorie
```

Da questa sezione puoi scegliere quanti scroll deve fare Selenium durante la scansione delle pagine categoria Amazon.

Valori consigliati:

```text
3-5 scroll   = test veloce
8-12 scroll  = uso normale consigliato
15-30 scroll = scansione profonda, più lenta e più pesante
```

Dopo il cambio, il bot cancella i buffer delle categorie attive così il prossimo refill usa subito la nuova profondità di scansione.


## 📥 Fonti Telegram

La V3.8 aggiunge una sezione nella dashboard:

`🛠️ Funzioni → 📥 Fonti Telegram`

Da qui puoi inserire il nome di un canale Telegram pubblico, per esempio:

```text
@nomecanale
```

Il bot legge la preview pubblica `https://t.me/s/nomecanale`, cerca link Amazon, recupera l’ASIN e ricostruisce l’offerta con il tuo scraper, il tuo tag affiliato e i tuoi controlli qualità.

Le offerte valide vengono salvate nel buffer della categoria:

```text
Fonti Telegram 📥
```

La funzione non copia il testo degli altri canali: usa i link solo come segnalazione e rigenera il post con il formato del tuo bot.


## V3.9 - Fix ASIN da fonti Telegram

- Migliorata estrazione ASIN da canali Telegram pubblici.
- Supporto link Amazon senza protocollo, link codificati, bottoni inline e short-link amzn.to/amzn.eu/a.co.
- Aggiunto parser ASIN diretto dal testo del messaggio.
- Aggiunti log dettagliati per link trovati, ASIN trovati, prodotti aggiunti e scartati.

## Fonti Telegram avanzate con Telethon

La funzione `📥 Fonti Telegram` può lavorare in due modalità:

1. **Preview pubblica**: legge `https://t.me/s/nomecanale`. Non richiede login, ma funziona solo con canali pubblici e può perdere bottoni/link nascosti.
2. **Telethon/account utente**: usa un account Telegram reale per leggere messaggi, bottoni inline, foto e anche chat/bot a cui l'account ha accesso.

### Configurazione Telethon

1. Vai su `https://my.telegram.org`.
2. Accedi con un numero Telegram, meglio se dedicato.
3. Entra in **API development tools**.
4. Crea una app e copia `api_id` e `api_hash`.
5. Nel file `.env` imposta:

```env
TELETHON_ENABLED=false
TELETHON_API_ID=123456
TELETHON_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELETHON_SESSION=telegram_user.session
```

6. Esegui il login una sola volta:

```powershell
.\venv\Scripts\python.exe telethon_login.py
```

7. Dopo il login, abilita Telethon:

```env
TELETHON_ENABLED=true
```

8. Riavvia il bot:

```powershell
.\venv\Scripts\python.exe main.py
```

### Note importanti

- Non pubblicare mai il file `telegram_user.session`: equivale a una sessione Telegram attiva.
- Usa preferibilmente un account Telegram dedicato, non il tuo personale.
- L'account deve poter accedere al canale, gruppo o bot che vuoi leggere.
- Il bot non copia il testo originale: usa link/ASIN/prezzo/foto come segnale e ricostruisce il post con il tuo formato.


### Foto Fonti Telegram

Le foto importate tramite Telethon vengono salvate in `telegram_source_media` per restare disponibili anche dopo il refill. Nel tuo `.env` puoi configurare:

```env
TELEGRAM_SOURCE_MEDIA_PATH=telegram_source_media
```

Non eliminare questa cartella se hai offerte Telegram ancora nel buffer.

### Paginazione categorie

Da `⚙️ Impostazioni → 📄 Pagine categorie` puoi decidere quante pagine Amazon deve aprire per ogni categoria.

Il bot ora lavora così:

1. apre la categoria Amazon;
2. fa gli scroll configurati;
3. prova ad aprire la pagina successiva;
4. ripete fino al numero massimo di pagine impostato.

Valori consigliati:

- `1 pagina`: test veloce;
- `2-3 pagine`: uso normale consigliato;
- `4-5 pagine`: scansione più profonda;
- `7-10 pagine`: molto pesante, maggiore rischio CAPTCHA.

Formula pratica:

```text
lavoro totale ≈ pagine categorie × scroll categorie
```

Esempio: `3 pagine × 8 scroll = 24 passaggi di scansione per categoria`.
