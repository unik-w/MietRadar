# -*- coding: utf-8 -*-
"""
test_llm_personalizer.py
========================
Interactive test module for the Gemma-based LLM personaliser.

What it does:
  1. Opens a real WG-Gesucht search results page (from your .env search URL)
  2. Extracts up to N listing URLs from the page
  3. For each listing: scrapes the description and poster name via Selenium
  4. Feeds everything into llm_personalizer.personalise_message()
  5. Prints each generated message for your review

Usage:
  # activate your venv first
  cd /Users/Q662452/Desktop/immo
  python test_llm_personalizer.py

Optional flags:
  --listings <N>   How many listings to process (default: 3)
  --search-url     Override the search URL from .env
  --no-llm         Skip LLM, just show the plain template substitution

The script prints a summary at the end with all generated messages so
you can read and approve each one before any are actually sent.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import textwrap
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# ── Include src/ in Python Path ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Load environment ─────────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(dotenv_path=env_path)

WG_SEARCH_URLS = os.getenv("WG_SEARCH_URLS", "")
WG_EMAIL       = os.getenv("WG_EMAIL", "")
WG_PASSWORD    = os.getenv("WG_PASSWORD", "")

HERE = Path(__file__).parent
MESSAGE_TEMPLATE = (HERE.parent / "config" / "message.txt").read_text(encoding="utf-8")

# ── Colours for terminal output ───────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"
RED    = "\033[91m"


def banner(text: str, colour: str = CYAN) -> None:
    width = 72
    print(f"\n{colour}{BOLD}{'─' * width}")
    print(f"  {text}")
    print(f"{'─' * width}{RESET}\n")


# ── Selenium helpers (reuse from submit_wg) ───────────────────────────────────

def create_driver():
    """Create a headless-compatible stealth Chrome driver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=de-DE")
    options.add_argument("--window-size=1280,900")

    # Shared profile so we can log in once
    profile_dir = str(HERE.parent / "data" / "wgbot_profile")
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
        """
    })
    return driver


def random_sleep(min_s: float = 1.0, max_s: float = 3.0) -> None:
    import random
    time.sleep(random.uniform(min_s, max_s))


def ensure_logged_in(driver) -> None:
    """Navigate to homepage, accept cookies, log in if needed."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException

    driver.get("https://www.wg-gesucht.de/")
    random_sleep(2, 3)

    # Accept cookies
    try:
        banners = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'Akzeptieren') or contains(text(),'Accept all') or contains(text(),'Alle akzeptieren')]"
        )
        if banners:
            banners[0].click()
            random_sleep(1, 2)
            print(f"  {GREEN}🍪 Cookie banner accepted.{RESET}")
    except Exception:
        pass

    # Check if already logged in
    page_src = driver.page_source.lower()
    if any(kw in page_src for kw in ["abmelden", "meine-anzeigen", "ausloggen"]):
        print(f"  {GREEN}✅ Already logged in.{RESET}")
        return

    if not WG_EMAIL or not WG_PASSWORD:
        print(f"  {YELLOW}⚠ No WG credentials in .env — skipping login.{RESET}")
        return

    # Log in
    try:
        konto_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'Mein Konto')]")
        if konto_btns:
            konto_btns[0].click()
            random_sleep(1, 2)

            wait = WebDriverWait(driver, 10)
            email_field = wait.until(EC.visibility_of_element_located((By.ID, "login_email_username")))
            email_field.clear()
            email_field.send_keys(WG_EMAIL)
            random_sleep(0.3, 0.7)

            pwd_field = driver.find_element(By.ID, "login_password")
            pwd_field.clear()
            pwd_field.send_keys(WG_PASSWORD)
            random_sleep(0.3, 0.7)

            driver.find_element(By.ID, "login_submit").click()
            random_sleep(3, 5)
            print(f"  {GREEN}🔑 Logged in.{RESET}")
    except Exception as e:
        print(f"  {YELLOW}⚠ Login attempt failed: {e}{RESET}")


def scrape_listing_urls(driver, search_url: str, max_listings: int) -> list[str]:
    """
    Visit the search results page and collect listing hrefs.
    Returns a list of absolute URLs.
    """
    from selenium.webdriver.common.by import By

    print(f"\n🔍 Fetching search results from:\n   {DIM}{search_url}{RESET}\n")
    driver.get(search_url)
    random_sleep(2, 4)

    # wg-gesucht uses h2.truncate_title a for listings
    anchors = driver.find_elements(By.CSS_SELECTOR, "h2.truncate_title a, h3.truncate_title a")
    urls = []
    for a in anchors:
        href = a.get_attribute("href") or ""
        if (("/wg-zimmer-" in href or "/wohnungen-" in href) and "asset_id" not in href):
            if href not in urls:
                urls.append(href)
        if len(urls) >= max_listings:
            break

    print(f"  Found {len(urls)} matching listing(s) on that page.")
    return urls


def scrape_listing_details(driver, listing_url: str) -> dict:
    """
    Visit a listing page and extract:
      - description text
      - poster name
      - listing title
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import NoSuchElementException, TimeoutException

    result = {
        "url": listing_url,
        "title": "",
        "description": "",
        "poster_name": "",
    }

    driver.get(listing_url)
    random_sleep(2, 4)

    # Title
    try:
        result["title"] = driver.title.strip()
    except Exception:
        pass

    # Description
    import re
    full_desc = []
    for _id, title in [("freitext_0", "Zimmer:"), ("freitext_1", "Lage:"), ("freitext_2", "WG-Leben:"), ("freitext_3", "Sonstiges:")]:
        try:
            el = driver.find_element(By.ID, _id)
            # Use textContent because inactive tabs have display:none
            raw_text = el.get_attribute("textContent") or ""
            # Strip out annoying embedded js tags
            clean_text = re.sub(r"googletag\.cmd\.push\([\s\S]*?\}\);", "", raw_text).strip()
            if clean_text:
                full_desc.append(f"--- {title} ---\n{clean_text}")
        except NoSuchElementException:
            pass
            
    if full_desc:
        result["description"] = "\n\n".join(full_desc)
    else:
        for sel in ["#ad_description_text", ".ad_description_text", ".description"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                result["description"] = el.text.strip()
                break
            except NoSuchElementException:
                continue
    
        if not result["description"]:
            # Fallback: look for any large text block
            try:
                paras = driver.find_elements(By.CSS_SELECTOR, "p")
                texts = [p.text.strip() for p in paras if len(p.text.strip()) > 80]
                result["description"] = "\n\n".join(texts[:5])
            except Exception:
                pass

    # ── Poster name: scrape from /nachricht-senden/ page first ──────────────
    # listing_url may be a full URL (https://...) or a relative path.
    # Extract just the path component so the nachricht URL is correct.
    try:
        from urllib.parse import urlparse
        parsed = urlparse(listing_url)
        path = parsed.path  # e.g. /wg-zimmer-in-Muenchen-....html
    except Exception:
        path = "/" + listing_url.lstrip("/")
    nachricht_url = f"https://www.wg-gesucht.de/nachricht-senden{path}"

    NOT_A_NAME = {
        'seite', 'anmelden', 'registrieren', 'suche', 'anzeige', 'wg',
        'zimmer', 'wohnung', 'nachricht', 'senden', 'kontakt', 'login',
        'startseite', 'home', 'back', 'weiter', 'menu', 'mehr', 'alle',
        'zur', 'von', 'nach', 'für', 'dem', 'der', 'die', 'das', 'profil',
    }

    try:
        driver.get(nachricht_url)
        random_sleep(2, 4)

        # Exact structure found on WG-Gesucht Nachricht page (from HTML inspection):
        # <div class="ml10">
        #   <b>Monika</b>
        #   <br>
        #   Mitglied seit: ...
        # </div>
        # Strategy: find any element containing 'Mitglied seit' raw text,
        # then get the <b> (or strong) child of the same container.

        found_name = ""

        # XPath: find the div/container that holds 'Mitglied seit' text,
        # then grab the first <b> child of that container.
        for xp in [
            "//div[contains(@class, 'ml10') and contains(., 'Mitglied seit')]//b[1]",
            "//div[contains(@class, 'ml10') and contains(., 'Mitglied seit')]//strong[1]",
            "//div[contains(@class, 'conversation_ad_card_avatar')]/following-sibling::div//b[1]",
            "//div[contains(@class, 'conversation_ad_card_avatar')]/following-sibling::div//strong[1]",
            "//div[contains(@class, 'ml10')]//b[1]",
            "//div[contains(@class, 'ml10')]//strong[1]",
        ]:
            try:
                candidates = driver.find_elements(By.XPATH, xp)
                for el in candidates:
                    raw_text = el.text.strip()
                    if not raw_text:
                        continue
                        
                    words = raw_text.split()
                    if words[0].lower() in ["herr", "frau"] and len(words) > 1:
                        text = f"{words[0]} {words[1]}"
                    elif len(words) > 1 and words[0].endswith("."):
                        text = f"{words[0]} {words[1]}"
                    else:
                        text = words[0]
                        
                    if len(text) >= 3 and text[0].isupper() and text.replace(" ", "").replace("-", "").replace(".", "").isalpha() and text.lower() not in NOT_A_NAME:
                        found_name = text
                        break
                if found_name:
                    break
            except Exception:
                continue

        if found_name:
            result["poster_name"] = found_name

    except Exception:
        pass

    # Fallback: regex scan through listing description (temporarily disabled for testing)
    # if not result["poster_name"] and result["description"]:
    #     patterns = [
    #         r'(?:ich bin|mein Name ist|ich heiße)\s+([A-ZÄÖÜ][a-zäöüß]+)',
    #         r'mit mir,?\s+([A-ZÄÖÜ][a-zäöüß]+)',
    #         r'bin\s+([A-ZÄÖÜ][a-zäöüß]+)\s*[,\.!\)]',
    #         r'(?:LG|VG|Liebe Grüße|Viele Grüße|Grüße|Best),?\s+([A-ZÄÖÜ][a-zäöüß]+)',
    #         r'Ich,?\s+([A-ZÄÖÜ][a-zäöüß]+),',
    #         r'Mitbewohner(?:in)?\s+([A-ZÄÖÜ][a-zäöüß]+)',
    #     ]
    #     false_positives = {
    #         'Hallo', 'Sehr', 'Bitte', 'Danke', 'Gerne', 'Liebe',
    #         'Suche', 'Biete', 'Zimmer', 'Wohnung', 'Haus', 'Freue',
    #         'Guten', 'Hier', 'Diese', 'Mein', 'Dein', 'Willkommen',
    #     }
    #     for pattern in patterns:
    #         m = re.search(pattern, result["description"], re.IGNORECASE)
    #         if m:
    #             name = m.group(1).strip()
    #             if name not in false_positives and len(name) >= 2:
    #                 result["poster_name"] = name
    #                 break

    return result


# ── Main test flow ─────────────────────────────────────────────────────────────

def run_test(max_listings: int = 3, search_url: str = "", use_llm: bool = True) -> None:
    if not search_url:
        # Take the first URL from the .env list
        urls_env = WG_SEARCH_URLS.split(",")
        search_url = urls_env[0].strip() if urls_env else ""

    if not search_url:
        print(f"{RED}❌ No search URL found!  Set WG_SEARCH_URLS in .env or pass --search-url.{RESET}")
        sys.exit(1)

    banner(f"🏠 WG-Gesucht LLM Personaliser — Test Run  ({datetime.now():%Y-%m-%d %H:%M})")
    print(f"  Listings to test : {max_listings}")
    print(f"  LLM enabled      : {use_llm}")
    print(f"  Search URL       : {DIM}{search_url[:80]}…{RESET}")

    driver = create_driver()
    results = []

    try:
        ensure_logged_in(driver)

        listing_urls = scrape_listing_urls(driver, search_url, max_listings)
        if not listing_urls:
            print(f"{YELLOW}⚠ No listings found on that page.  Check your search URL.{RESET}")
            return

        for idx, url in enumerate(listing_urls, 1):
            banner(f"Listing {idx}/{len(listing_urls)}", colour=YELLOW)
            print(f"  URL: {DIM}{url}{RESET}\n")

            details = scrape_listing_details(driver, url)

            print(f"  {BOLD}Title      :{RESET} {details['title']}")
            print(f"  {BOLD}Poster name:{RESET} {details['poster_name'] or '(not found)'}")
            max_desc_chars = int(os.getenv("LLM_MAX_DESC_CHARS", "1200"))
            print(f"\n  {BOLD}--- Description preview (first {max_desc_chars} chars) ---{RESET}")
            desc_preview = details["description"][:max_desc_chars] or "(no description found)"
            print(textwrap.fill(desc_preview, width=70, initial_indent="  ", subsequent_indent="  "))
            print()

            if use_llm:
                print(f"  {CYAN}Generating personalised message…{RESET}")
                from llm_personalizer import personalise_message
                msg = personalise_message(
                    template=MESSAGE_TEMPLATE,
                    listing_description=details["description"],
                    poster_name=details["poster_name"],
                    listing_url=url,
                )
            else:
                # Plain substitution only
                msg = MESSAGE_TEMPLATE
                if details["poster_name"]:
                    msg = msg.replace("{name}", details["poster_name"])
                else:
                    msg = msg.replace(" {name}", "").replace("{name}", "")
                msg = msg.replace("{LLM_TEXT}", "")
                import re
                msg = re.sub(r'\n{3,}', '\n\n', msg)
            print(f"\n  {GREEN}{BOLD}--- Generated Message ---{RESET}")
            for line in msg.splitlines():
                print(f"  {line}")

            results.append({
                "index": idx,
                "url": url,
                "title": details["title"],
                "poster_name": details["poster_name"],
                "description_snippet": details["description"][:200],
                "message": msg,
            })

            random_sleep(1, 2)

    finally:
        driver.quit()

    # ── Summary report ───────────────────────────────────────────────────────
    banner("📋  SUMMARY — All Generated Messages", colour=GREEN)
    for r in results:
        print(f"{BOLD}[{r['index']}] {r['title']}{RESET}")
        print(f"    URL     : {DIM}{r['url']}{RESET}")
        print(f"    Poster  : {r['poster_name'] or '(unknown)'}")
        print(f"\n{r['message']}")
        print(f"\n{'─' * 72}\n")

    print(f"{GREEN}✅  Test complete.  {len(results)} message(s) generated.{RESET}")
    print(f"{DIM}   Review the messages above before enabling the bot.{RESET}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test the Gemma-based LLM message personaliser against real WG-Gesucht listings."
    )
    parser.add_argument(
        "--listings",
        type=int,
        default=3,
        metavar="N",
        help="Number of listings to fetch and personalise (default: 3)"
    )
    parser.add_argument(
        "--search-url",
        type=str,
        default="",
        metavar="URL",
        help="Override the WG_SEARCH_URLS from .env"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM — just show plain template substitution (useful for testing scraping)"
    )

    args = parser.parse_args()

    run_test(
        max_listings=args.listings,
        search_url=args.search_url,
        use_llm=not args.no_llm,
    )
