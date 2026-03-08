# WG-Ninja 🥷
An advanced, stealth-based Python bot for automating WG-Gesucht applications. Features a native Selenium Chromedriver to bypass bot protection, smart agency blacklisting, and integrates Google Gemma 3 4B-IT (via HuggingFace) to dynamically parse listings and generate highly personalized, human-like rental applications at scale.

**Disclaimer**: Use at your own risk. Respect the platforms' terms of service. This tool is provided "as is" without warranty.

## Features
- **Zero-Touch Operation**: Scans WG-Gesucht periodically (e.g., every 10 minutes), navigating through multiple pages to find the freshest listings.
- **Stealth & Clean Sessions**: Utilizes headless Chrome via `webdriver_manager` with a dedicated unique profile, bypassing many standard bot checks.
- **Smart Blacklisting**: 
  - *Automatic Check*: Immediately blacklists sent applications to prevent spam.
  - *Commercial Block*: Actively skips commercial/corporate agencies (like HousingAnywhere, Spacest, Medici) before even clicking the listing.
  - *Manual Override*: You can drop links into a text file to ensure they are ignored.
- **LLM Personalisation** (Optional): Harnesses local AI (`Gemma 3 4B-IT` via HuggingFace on PyTorch) to read listing descriptions and dynamically generate highly personalized application paragraphs, inserting them alongside your standard template.

## Directory Structure
To keep the application cleanly segmented, the repository follows a professional structure:
```text
wg-ninja/
├── config/                 # ALL user-editable configurations
│   ├── .env                # Secrets (credentials, URLs, HuggingFace token)
│   ├── llm_persona.txt     # The prompt/identity used by the LLM (editable)
│   ├── manual_blacklist.txt# Paste links here to ignore them
│   └── message.txt         # Your main application message template
├── data/                   # Dynamic memory & temp storage (DO NOT EDIT)
│   ├── wg_diff.dat         # Record of processed IDs
│   ├── wg_sent_request.dat # Audit log with timestamps
│   ├── wg_offer.json       # Debug snapshot of current scan
│   └── wgbot_profile/      # Browser session cache (cookies)
├── scripts/                # Setup & utility bash scripts
│   └── setup.sh            
├── src/                    # Core Python application logic
│   ├── wg-gesucht.py       # Main Application Loop
│   ├── submit_wg.py        # Browser automation & interactions
│   └── llm_personalizer.py # LLM inference and text synthesis
├── tests/                  # Unit tests and isolated scripts
│   └── ...
├── README.md               # Documentation
└── requirements.txt        # Python dependencies
```

## Setup & Installation

### 1. Prerequisites
- Python 3.10+
- Chrome Browser installed on your machine.

### 2. Install Packages
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration
1. **Environment Variables**:
   Copy `config/.env.example` to `config/.env`.
   Fill out your WG-Gesucht login details (`WG_EMAIL`, `WG_PASSWORD`) and define your `WG_SEARCH_URLS`.
   *(Optional)* Fill out `HF_TOKEN` if you are using LLM Personalisation.

2. **Your Message**:
   Edit `config/message.txt`. Use `{name}` where you want the bot to automatically insert the landlord's name.
   
3. **LLM Persona (Optional)**:
   If using the AI feature (`USE_LLM_PERSONALIZATION=true` in `.env`), edit `config/llm_persona.txt` to give the AI context about who you are and what you care about.

4. **Manual Blacklist**:
   If there is an ad you hate, copy the end of its URL (e.g., `/wg-zimmer-in-Muenchen.12345.html`) and paste it into `config/manual_blacklist.txt`.

### 4. First Run & CAPTCHAs 🧩
When you run the bot for the first time, WG-Gesucht will likely present a **CAPTCHA**.
- The bot is configured to detect this and will **pause** execution.
- A Chrome window will remain open. You must **manually solve the CAPTCHA** in that window.
- Once solved, the bot will automatically detect the page change and continue its work.
- Subsequent runs should be smoother as session cookies are saved in `data/wgbot_profile/`.

## Running the Bot

Run the bot directly from the root directory:
```bash
python src/wg-gesucht.py
```
Leave it running! It will scan up to 10 pages in the background, send messages precisely when needed, update local blacklists, and then sleep based on the `CHECK_INTERVAL_SECONDS` defined in your `.env`.

## Testing the Bot Offline

You can test individual components before unleashing the bot on live listings!

### 1. Test LLM Personalisation (`test_llm_personalizer.py`)
This test simulates the entire pipeline for the LLM. It fetches a few listings from WG-Gesucht, scrapes them, and uses your `config/llm_persona.txt` to generate an AI response injected into your `config/message.txt` placeholder `{LLM_TEXT}`. **It will not send the message, it will only print it to the terminal.**
```bash
python tests/test_llm_personalizer.py --listings 1
```

### 2. Test Browser Stealth (`test_stealth.py`)
Because WG-Gesucht employs bot protection, the Chrome instance must be properly disguised. Run this test to ensure your headless Chrome configuration passes typical bot-checks.
```bash
python tests/test_stealth.py
```
