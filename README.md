# Amazon Offers Telegram Bot

A Telegram bot for publishing Amazon affiliate offers with categories, scheduling, anti-duplicate checks and clean offer posts.

> Community version of an Amazon affiliate offer publisher for Telegram channels.

---

## Overview

Amazon Offers Telegram Bot is a Python-based Telegram bot designed to help manage and publish Amazon affiliate offers.

The bot can collect products, organize them by category, generate clean offer messages, create product images, avoid duplicate ASINs, and publish offers automatically on Telegram.

This repository contains the **Community version**.

---

## Features

- Telegram offer publishing
- Amazon affiliate links
- Category-based posting
- Automatic scheduler
- Anti-duplicate ASIN checks
- Clean Telegram post formatting
- Offer image generation
- Manual Amazon link posting
- Buffer-based offer queue
- Basic offer quality filtering
- Configurable `.env` settings
- Windows-friendly startup scripts
- Selenium-based scraping support
- Amazon PA-API support where configured

---

## Example Telegram Post

```text
🔥 Offerta Amazon

Product title here...

💶 Prezzo: 19,99€
🏷️ Coupon: 10%

📦 Casa e cucina • #Casaecucina • #pubblicità
```

The goal is to keep posts clean, readable and suitable for Telegram channels.

---

## Project Structure

```text
amazon-offers-telegram-bot/
├─ main.py
├─ requirements.txt
├─ .env.example
├─ README.md
├─ CHANGELOG_V2.md
├─ README_AVVIO_V2.md
├─ avvia_bot.ps1
├─ installa_dipendenze.ps1
└─ src/
   ├─ assets/
   ├─ buffer/
   ├─ configs/
   ├─ database/
   ├─ handlers/
   ├─ promotions/
   ├─ scraper/
   ├─ telegram/
   ├─ tracking/
   └─ utils/
```

---

## Requirements

Before running the bot, you need:

- Python 3.11 or newer
- A Telegram Bot Token
- An Amazon Partner Tag
- Amazon PA-API credentials, if using PA-API features
- Google Chrome installed, if using Selenium scraping
- Windows PowerShell, if using the included `.ps1` scripts

---

## Installation on Windows

Clone the repository:

```powershell
git clone https://github.com/castieldev85/amazon-offers-telegram-bot.git
cd amazon-offers-telegram-bot
```

Create a virtual environment:

```powershell
python -m venv venv
```

Upgrade pip:

```powershell
.\venv\Scripts\python.exe -m pip install --upgrade pip
```

Install dependencies:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create your environment file:

```powershell
copy .env.example .env
notepad .env
```

Start the bot:

```powershell
.\venv\Scripts\python.exe main.py
```

---

## PowerShell Script Execution

If Windows blocks the `.ps1` scripts, you can allow script execution only for the current PowerShell session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then you can run:

```powershell
.\avvia_bot.ps1
```

This change is temporary and applies only to the current PowerShell window.

---

## Configuration

Copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```

Then edit the `.env` file:

```powershell
notepad .env
```

Example configuration:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789

AMAZON_PAAPI_ACCESS_KEY=your_amazon_access_key
AMAZON_PAAPI_SECRET_KEY=your_amazon_secret_key
AMAZON_PARTNER_TAG=yourtag-21

BOT_BRAND_TEXT=t.me/your_channel
```

Never publish your real `.env` file.

---

## Important Security Notes

Do not commit or publish:

- `.env`
- Telegram bot tokens
- Amazon API keys
- real Amazon Partner Tags, if you want to keep them private
- user data files
- logs
- local databases
- buffer files
- generated runtime files
- private configuration files

The repository includes `.gitignore` rules to help prevent this.

---

## Telegram Bot Setup

To create a Telegram bot:

1. Open Telegram.
2. Search for `@BotFather`.
3. Create a new bot with `/newbot`.
4. Copy the bot token.
5. Paste the token into your `.env` file:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
```

You also need your Telegram user ID as admin:

```env
ADMIN_IDS=123456789
```

---

## Amazon Affiliate Setup

To publish affiliate links, configure your Amazon Partner Tag:

```env
AMAZON_PARTNER_TAG=yourtag-21
```

If you use Amazon PA-API features, also configure:

```env
AMAZON_PAAPI_ACCESS_KEY=your_amazon_access_key
AMAZON_PAAPI_SECRET_KEY=your_amazon_secret_key
```

Make sure you follow the rules of the Amazon Associates Program.

---

## Categories

The bot supports category-based offer handling.

Example categories may include:

- Abbigliamento
- Elettronica
- Casa e cucina
- Bellezza
- Sport
- Giocattoli
- Fai da te
- Auto e Moto
- Libri e Kindle
- Videogiochi
- Alimentari
- Animali
- Offerte Amazon
- Offerte del giorno
- Offerte lampo

Categories can be extended or customized in the scraper and configuration files.

---

## Offer Quality Filtering

The bot includes basic offer filtering to reduce low-quality posts.

It can check:

- current price
- old price validity
- discount credibility
- coupon data
- promo code validity
- duplicate ASINs
- previously published products

The Community version includes a basic quality layer.

Advanced scoring and deeper validation may be part of custom or Pro versions.

---

## Duplicate Protection

The bot is designed to avoid repeatedly publishing the same Amazon ASIN too frequently.

Duplicate protection can help prevent:

- repeated offers
- spam-like posting
- low-quality channel experience
- poor user engagement

---

## Buffer System

The bot uses a buffer-based publishing flow.

Typical flow:

```text
Scraper / PA-API
↓
Buffer
↓
Quality check
↓
Anti-duplicate check
↓
Telegram post
```

This makes it easier to collect offers first and publish them later according to the scheduler.

---

## Scheduler

The scheduler can automatically publish offers based on configured intervals and active settings.

Depending on configuration, the bot can handle:

- automatic posting
- category-based posting
- scheduled time windows
- refill cycles
- offer queue management

---

## Image Generation

The bot can generate clean offer images containing product information such as:

- product image
- current price
- discount badge, when reliable
- brand/channel text

Images should remain simple and readable.

Avoid showing unreliable data such as fake old prices or unrealistic discounts.

---

## Running the Bot

Manual start:

```powershell
.\venv\Scripts\python.exe main.py
```

Using the helper script:

```powershell
.\avvia_bot.ps1
```

---

## Updating Dependencies

To update dependencies:

```powershell
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt --upgrade
```

---

## GitHub Community Version

This repository is intended as a Community version.

It provides a clean starting point for developers who want to build a Telegram bot for Amazon affiliate offers.

It is not intended to expose private tokens, private business logic, paid features, or production secrets.

---

## Pro / Custom Version

A more advanced private version may include:

- Advanced offer scoring
- Multi-channel publishing
- Local statistics dashboard
- Category performance reports
- Better scheduling logic
- Smart posting strategies
- Advanced buffer cleanup
- Better invalid-offer quarantine
- More advanced image templates
- Assisted setup and customization

For custom setup, Pro features, or commercial customization, contact the maintainer.

---

## Suggested Use Cases

This bot can be useful for:

- Telegram deal channels
- Amazon affiliate publishers
- small community offer groups
- category-specific deal channels
- personal automation experiments
- learning Telegram bot development

---

## What This Project Does Not Do

This project does not guarantee:

- Amazon sales
- affiliate commissions
- product availability
- price accuracy forever
- compliance with every regional affiliate rule
- protection against Amazon layout changes

Always verify your final configuration and follow Amazon Associates policies.

---

## Disclaimer

This project is not affiliated with Amazon.

Amazon and all related marks are trademarks of Amazon.com, Inc. or its affiliates.

Use this project responsibly and follow the Amazon Associates Program policies.

The maintainer is not responsible for misuse, policy violations, account restrictions, incorrect prices, or third-party changes.

---

## License

This project is **not open source**.

The source code is publicly visible for review, learning, and evaluation purposes only.

All rights are reserved. You are not allowed to sell, resell, redistribute, repackage, host, publish, or commercially exploit this software or modified versions without explicit written permission from the copyright holder.

See the [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions, suggestions and improvements are welcome.

Before opening a pull request:

- do not include secrets
- do not include `.env`
- do not include logs
- do not include user data
- keep changes clean and documented

---

## Support

For questions, custom setup, or Pro features, contact the maintainer through the preferred contact channel.