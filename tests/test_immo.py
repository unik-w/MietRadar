# -*- coding: utf-8 -*-
"""
test_immo.py
============
End-to-end test for the ImmobilienScout24 bot.
Mirrors test_llm_personalizer.py but for IS24.

What it does:
  1. Opens IS24, logs in via the multi-step flow
  2. Scrapes search results from IMMO_SEARCH_URLS
  3. For each listing: extracts description + contact info
  4. Generates a personalised message (with/without LLM)
  5. Prints all messages for review — does NOT send

Usage:
  cd /Users/Q662452/Desktop/immo
  python tests/test_immo.py
  python tests/test_immo.py --listings 5
  python tests/test_immo.py --no-llm
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

IMMO_SEARCH_URLS = os.getenv("IMMO_SEARCH_URLS", "")

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

# Sponsored / advert keywords to skip
SKIP_KEYWORDS = [
    "gesponsert", "anzeige", "projekteinheit", "bauprojekt",
    "gold partner", "wohnungsswap", "premium partner",
]


def banner(text: str, colour: str = CYAN) -> None:
    width = 72
    print(f"\n{colour}{BOLD}{'─' * width}")
    print(f"  {text}")
    print(f"{'─' * width}{RESET}\n")


def scrape_listing_ids(driver, search_url: str, max_listings: int) -> list[str]:
    """
    Visit the IS24 search results page and collect expose IDs.
    Filters out sponsored/partner ads.
    """
    from selenium.webdriver.common.by import By
    from submit_wg import random_sleep
    from submit_immo import solve_captcha_if_present

    print(f"\n🔍 Fetching search results from:\n   {DIM}{search_url[:80]}…{RESET}\n")
    driver.get(search_url)
    random_sleep(3, 5)

    # Dismiss cookie banner
    try:
        banners = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'Alle akzeptieren') or contains(text(),'Akzeptieren')]"
        )
        if banners:
            banners[0].click()
            random_sleep(1, 2)
    except Exception:
        pass

    solve_captcha_if_present(driver)

    expose_ids = []
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/expose/']")

    for link in links:
        try:
            href = link.get_attribute("href") or ""
            if "/expose/" not in href:
                continue

            # Check parent for sponsored markers
            try:
                card = link.find_element(By.XPATH,
                    "./ancestor::li | ./ancestor::article | ./ancestor::div[contains(@class,'result-list')]"
                )
                card_text = card.text.lower()
            except Exception:
                card_text = ""

            if any(kw in card_text for kw in SKIP_KEYWORDS):
                print(f"  ⏭  Skipping sponsored ad: {href[:60]}…")
                continue

            m = re.search(r'/expose/(\d+)', href)
            if m:
                eid = m.group(1)
                if eid not in expose_ids:
                    expose_ids.append(eid)

            if len(expose_ids) >= max_listings:
                break
        except Exception:
            pass

    print(f"  Found {len(expose_ids)} matching listing(s) (excluding sponsored).")
    return expose_ids


def scrape_expose_details(driver, expose_id: str) -> dict:
    """
    Visit an IS24 expose page and extract:
      - title
      - description text
      - poster/contact name
    """
    from submit_immo import extract_listing_info

    expose_url = f"https://www.immobilienscout24.de/expose/{expose_id}"

    result = {
        "expose_id": expose_id,
        "url": expose_url,
        "title": "",
        "description": "",
        "poster_name": "",
    }

    # extract_listing_info navigates to the expose page internally
    info = extract_listing_info(driver, expose_url)
    result["description"] = info.get("description", "")
    result["poster_name"] = info.get("poster_name", "")

    # Title — we're already on the expose page after extract_listing_info
    try:
        result["title"] = driver.title.strip()
    except Exception:
        pass

    return result


# ── Main test flow ─────────────────────────────────────────────────────────────

def run_test(max_listings: int = 3, search_url: str = "", use_llm: bool = True) -> None:
    if not search_url:
        urls_env = IMMO_SEARCH_URLS.split(",")
        search_url = urls_env[0].strip() if urls_env else ""

    if not search_url:
        print(f"{RED}❌ No search URL found!  Set IMMO_SEARCH_URLS in .env or pass --search-url.{RESET}")
        sys.exit(1)

    banner(f"🏠 ImmobilienScout24 Bot — Test Run  ({datetime.now():%Y-%m-%d %H:%M})")
    print(f"  Listings to test : {max_listings}")
    print(f"  LLM enabled      : {use_llm}")
    print(f"  Search URL       : {DIM}{search_url[:80]}…{RESET}")

    from submit_immo import init_driver_and_login
    from submit_wg import random_sleep
    driver = init_driver_and_login()
    results = []

    try:
        expose_ids = scrape_listing_ids(driver, search_url, max_listings)
        if not expose_ids:
            print(f"{YELLOW}⚠ No listings found on that page.  Check your search URL.{RESET}")
            return

        for idx, eid in enumerate(expose_ids, 1):
            banner(f"Listing {idx}/{len(expose_ids)} — Expose #{eid}", colour=YELLOW)

            details = scrape_expose_details(driver, eid)

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
                    listing_url=details["url"],
                )
            else:
                # Plain substitution only
                msg = MESSAGE_TEMPLATE
                if details["poster_name"]:
                    msg = msg.replace("{name}", details["poster_name"])
                else:
                    msg = msg.replace(" {name}", "").replace("{name}", "")
                msg = msg.replace("{LLM_TEXT}", "")
                msg = re.sub(r'\n{3,}', '\n\n', msg)

            print(f"\n  {GREEN}{BOLD}--- Generated Message ---{RESET}")
            for line in msg.splitlines():
                print(f"  {line}")

            results.append({
                "index": idx,
                "expose_id": eid,
                "url": details["url"],
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
        print(f"{BOLD}[{r['index']}] Expose #{r['expose_id']} — {r['title']}{RESET}")
        print(f"    URL     : {DIM}{r['url']}{RESET}")
        print(f"    Poster  : {r['poster_name'] or '(unknown)'}")
        print(f"\n{r['message']}")
        print(f"\n{'─' * 72}\n")

    print(f"{GREEN}✅  Test complete.  {len(results)} message(s) generated.{RESET}")
    print(f"{DIM}   Review the messages above before enabling the bot.{RESET}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test the ImmobilienScout24 bot against real IS24 listings."
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
        help="Override the IMMO_SEARCH_URLS from .env"
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
