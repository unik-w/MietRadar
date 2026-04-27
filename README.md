# MietRadar 📡
### Automated Apartment & WG Application Bot for Germany — WG-Gesucht + ImmobilienScout24

> **Scan. Apply. Move in.** — An AI-powered Python bot that monitors [WG-Gesucht](https://www.wg-gesucht.de) and [ImmobilienScout24](https://www.immobilienscout24.de), automatically applies to new rental listings, and personalises each application using a local LLM.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL](https://img.shields.io/badge/license-GPL-green.svg)](LICENSE)
[![Platforms](https://img.shields.io/badge/platforms-WG--Gesucht%20%7C%20ImmoScout24-orange.svg)](#features)

---

## What is MietRadar?

Finding an apartment in Germany — especially in cities like Munich, Berlin, or Hamburg — is brutally competitive. Listings disappear within minutes. **MietRadar** monitors WG-Gesucht and ImmobilienScout24 around the clock, instantly applies to new listings matching your search filters, and optionally uses **Google Gemma 3 4B-IT** (running locally on your machine) to write a personalised paragraph for each application.

**Keywords**: Wohnung Bot, WG Bot, apartment bot Germany, Wohnungssuche automatisieren, WG-Gesucht Bot, ImmobilienScout24 Bot, rental application automation, Mietwohnung Bot, automated apartment search Germany, Bewerbung automatisch senden

**Disclaimer**: Use at your own risk. Respect the platforms' terms of service. This tool is provided "as is" without warranty.

## Features

| Feature | Description |
|---|---|
| 🏠 **Dual Platform** | Supports both WG-Gesucht and ImmobilienScout24 |
| 🔄 **Zero-Touch Loop** | Scans search results every N minutes, applies to new listings automatically |
| 🥷 **Stealth Automation** | Chrome with anti-detection patches, human-like typing & clicking |
| 🚫 **Smart Blacklisting** | Auto-blocks sent listings, skips sponsored/agency ads, manual override lists |
| 🧠 **AI Personalisation** | Local Gemma 3 4B-IT generates a unique paragraph per listing (optional) |
| 📊 **Reply Tracking** | CSV reports with 🟢/🟡/🔴 status for each application |
| ⚡ **Shared Pipeline** | Single message template, LLM persona, and helper functions for both platforms |

## Directory Structure
```text
miet-radar/
├── config/                         # User-editable configurations
│   ├── .env                        # Secrets (credentials, URLs, HF token)
│   ├── .env.example                # Template for .env
│   ├── llm_persona.txt             # LLM prompt/identity (editable)
│   ├── message.txt                 # Shared message template (both bots)
│   ├── message.txt.example         # Template for message.txt
│   ├── wg_blacklist.txt            # Manual blacklist for WG-Gesucht
│   ├── wg_blacklist.txt.example    # Template
│   ├── immo_blacklist.txt          # Manual blacklist for ImmoScout24
│   └── immo_blacklist.txt.example  # Template
├── data/                           # Runtime data (auto-generated, DO NOT EDIT)
│   ├── wg_diff.dat                 # Processed WG IDs
│   ├── wg_sent_request.dat         # WG audit log with timestamps
│   ├── wg_replies_report.csv       # WG reply tracker
│   ├── wg_offer.json               # WG scan snapshot
│   ├── wgbot_profile/              # WG browser session cache
│   ├── immo_diff.dat               # Processed ImmoScout IDs
│   ├── immo_sent_request.dat       # ImmoScout audit log
│   ├── immo_replies_report.csv     # ImmoScout reply tracker
│   ├── immo_offer.json             # ImmoScout scan snapshot
│   └── immobot_profile/            # ImmoScout browser session cache
├── src/                            # Core Python application logic
│   ├── wg-gesucht.py               # Main loop — WG-Gesucht
│   ├── submit_wg.py                # WG browser automation & form submission
│   ├── check_replies.py            # WG reply tracker
│   ├── immoscout.py                # Main loop — ImmobilienScout24
│   ├── submit_immo.py              # IS24 browser automation & form submission
│   ├── check_replies_immo.py       # IS24 reply tracker
│   └── llm_personalizer.py         # Shared LLM inference (Gemma 3 4B-IT)
├── tests/                          # Tests (no live sends)
│   ├── test_llm_personalizer.py    # WG end-to-end test
│   ├── test_immo.py                # IS24 end-to-end test
│   ├── test_stealth.py             # Browser stealth validation
│   └── test_llm_only.py            # Isolated LLM test
├── scripts/
│   └── setup.sh                    # One-command setup script
├── README.md
└── requirements.txt
```

## Quick Start

### 1. Prerequisites
- Python 3.10+
- Google Chrome installed

### 2. Setup (One Command)
```bash
git clone https://github.com/unik-w/MietRadar
cd miet-radar
bash scripts/setup.sh
```
The setup script creates a virtual environment, installs all dependencies, and scaffolds config files from templates.

### 2b. Manual Install (Alternative)
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env
cp config/message.txt.example config/message.txt
```

### 3. Configuration
1. **Credentials** — Edit `config/.env`:
   - **WG-Gesucht**: `WG_EMAIL`, `WG_PASSWORD`, `WG_SEARCH_URLS`
   - **ImmoScout24**: `IMMO_EMAIL`, `IMMO_PASSWORD`, `IMMO_SEARCH_URLS` and personal details (`IMMO_FIRST_NAME`, `IMMO_LAST_NAME`, etc.) for the IS24 contact form.
   - *(Optional)* `HF_TOKEN` for LLM Personalisation.

2. **Message Template** — Edit `config/message.txt`:
   - Use `{name}` for the landlord's name (auto-filled)
   - Use `{LLM_TEXT}` for the AI-generated paragraph (auto-filled when LLM is enabled)

3. **LLM Persona** *(Optional)* — Edit `config/llm_persona.txt`:
   Set `USE_LLM_PERSONALIZATION=true` in `.env` to enable. The persona auto-adapts to WG vs. apartment listings.

4. **Blacklists**:
   - `config/wg_blacklist.txt` — paste WG-Gesucht URLs to ignore
   - `config/immo_blacklist.txt` — paste ImmoScout expose IDs to ignore

### 4. First Run & CAPTCHAs 🧩
On first run, the platform will likely show a **CAPTCHA**. The bot detects this and **pauses** — solve it manually in the browser window. Subsequent runs use saved session cookies from `data/wgbot_profile/` or `data/immobot_profile/`.

## Usage

### Run the WG-Gesucht Bot
```bash
source venv/bin/activate
python src/wg-gesucht.py
```

### Run the ImmobilienScout24 Bot
```bash
source venv/bin/activate
python src/immoscout.py
```

Both bots run independently as continuous loops. They scan multiple search result pages, apply to new listings, update blacklists, and sleep for `CHECK_INTERVAL_SECONDS` (configurable in `.env`).

## Reply Tracking 📊

Generate a report of all sent applications and their reply status:

| Command | Report File |
|---|---|
| `python src/check_replies.py` | `data/wg_replies_report.csv` |
| `python src/check_replies_immo.py` | `data/immo_replies_report.csv` |

**Status indicators:**
- 🟢 **Replied** — host has responded
- 🟡 **Pending** — no reply yet, sent < 3 days ago
- 🔴 **No Reply** — no response after 3+ days

## Testing (Dry Run)

Test the full pipeline **without sending** any messages:

```bash
# WG-Gesucht — scrape + LLM personalisation
python tests/test_llm_personalizer.py --listings 3

# ImmobilienScout24 — scrape + LLM personalisation
python tests/test_immo.py --listings 3

# ImmoScout24 — template only, no LLM (fast scraping test)
python tests/test_immo.py --no-llm --listings 3

# Browser stealth validation
python tests/test_stealth.py
```

---

## How It Works

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────────┐
│  Scan search │───▶│ Filter: skip │───▶│ Extract desc  │───▶│ Generate msg │
│  result pages│    │ sponsored &  │    │ + contact name│    │ (template +  │
│  (paginated) │    │ blacklisted  │    │ from expose   │    │  LLM paragraph│
└─────────────┘    └──────────────┘    └───────────────┘    └──────┬───────┘
                                                                   │
                                                                   ▼
                                                           ┌──────────────┐
                                                           │ Fill form &  │
                                                           │ send message │
                                                           │ (stealth)    │
                                                           └──────────────┘
```

---

## Credits & History 📜
This project is an advanced evolution of the original [immo](https://github.com/nickirk/immo) repository by [nickirk](https://github.com/nickirk). 

While **MietRadar** has been completely restructured and modernized with Python 3 support, dual-platform automation, Selenium stealth techniques, and LLM-based personalization, the original git history has been merged and preserved to ensure proper attribution to the foundational work of the original contributors.
