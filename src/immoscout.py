"""
immoscout.py
============
Main loop for the ImmobilienScout24 bot.
Mirrors wg-gesucht.py but adapted for IS24's search result structure.

Flow: Login → Scrape search results → Diff against previously seen →
      Filter blacklist → Apply to new listings → Sleep → Repeat
"""

import json
import os
import os.path
import time
import traceback
from datetime import datetime
from json import JSONDecodeError

from selenium.webdriver.common.by import By
from dotenv import load_dotenv

import submit_immo

# Load the environment from the config folder relative to this file
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env")
load_dotenv(dotenv_path=env_path)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))
fname = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "immo_offer.json")

# Sponsored / advert / swap keywords to skip
SKIP_KEYWORDS = [
    "gesponsert", "anzeige", "projekteinheit", "bauprojekt",
    "gold partner", "wohnungsswap", "wohnungswap", "premium partner",
    "tauschwohnung", "wohnungstausch",
]


def scrape_search_results(driver=None):
    """
    Navigate to IS24 search URL(s), paginate through results,
    and collect expose IDs. Filters out sponsored/partner ads.
    """
    close_driver = False
    if driver is None:
        driver = submit_immo.create_driver()
        close_driver = True

    search_urls_raw = os.getenv("IMMO_SEARCH_URLS", "")
    search_urls = [u.strip() for u in search_urls_raw.split(",") if u.strip()]

    if not search_urls:
        print("⚠️ IMMO_SEARCH_URLS not set in .env! Nothing to scrape.")
        return []

    all_expose_ids = set()
    pages_to_scrape = 10

    for search_url in search_urls:
        print(f"\n🔍 Scraping IS24 search: {search_url[:80]}…")

        for page in range(1, pages_to_scrape + 1):
            # ── Build paginated URL ──────────────────────────────────────────
            # IS24 uses ?pagenumber=N for pagination
            import re as _re
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

            parsed = urlparse(search_url)
            params = parse_qs(parsed.query)
            params["pagenumber"] = [str(page)]
            paginated_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

            print(f"  -> Scanning Page {page}...")
            driver.get(paginated_url)
            submit_immo.random_sleep(3, 5)

            # Dismiss cookie/consent banners (only needed on first page)
            if page == 1:
                try:
                    banners = driver.find_elements(
                        By.XPATH,
                        "//*[contains(text(),'Alle akzeptieren') or contains(text(),'Akzeptieren') or contains(text(),'Accept all')]"
                    )
                    if banners:
                        submit_immo.human_move_and_click(driver, banners[0])
                        submit_immo.random_sleep(1, 2)
                except Exception:
                    pass

            submit_immo.solve_captcha_if_present(driver)

            # ── Collect expose IDs: two-pass approach ──────────────────────
            # Pass 1: gather text from each expose's links (title, visible text)
            #   Each listing card has multiple <a> links (image, title, etc.)
            #   We only use link-level text — NOT parent card text, because
            #   parent containers can span multiple listings and cross-contaminate.
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='expose']")
            expose_texts = {}  # { expose_id: combined_text }

            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    # Match both /expose/XXXXX and ?exposeId=XXXXX formats
                    m = _re.search(r'/expose/(\d+)', href) or _re.search(r'exposeId=(\d+)', href)
                    if not m:
                        continue
                    eid = m.group(1)

                    # Accumulate text from every link pointing to this expose
                    link_text = (link.text or "").strip()
                    link_title = (link.get_attribute("title") or "").strip()

                    prev = expose_texts.get(eid, "")
                    expose_texts[eid] = f"{prev} {link_text} {link_title}"
                except Exception:
                    pass

            print(f"     Raw expose IDs found: {len(expose_texts)}")

            # Pass 2: filter out skip keywords, add clean IDs
            page_count = 0
            skipped_count = 0

            for eid, combined_text in expose_texts.items():
                if eid in all_expose_ids:
                    continue  # already seen from a previous page

                if any(kw in combined_text.lower() for kw in SKIP_KEYWORDS):
                    skipped_count += 1
                    continue

                all_expose_ids.add(eid)
                page_count += 1

            if skipped_count:
                print(f"     ⏭  Skipped {skipped_count} sponsored/swap ad(s).")
            print(f"     ✅ Found {page_count} new listings on this page.")

    expose_list = list(all_expose_ids)

    # Save current findings for reference
    with open(fname, 'w') as f:
        json.dump([{"expose_id": eid} for eid in expose_list], f, indent=2)

    print(f"\n📊 Total unique listings found: {len(expose_list)}")

    if close_driver:
        driver.quit()

    return expose_list


# ── Main loop ────────────────────────────────────────────────────────────────

while True:
    try:
        sleep_time = CHECK_INTERVAL

        # Login
        driver = submit_immo.init_driver_and_login()

        try:
            if os.path.isfile(fname):
                print("'immo_offer.json' file found.")
            else:
                print("No 'immo_offer.json' file found.")

            data = scrape_search_results(driver=driver)

            blacklist = []

            # Already sent list (immo_diff.dat)
            immo_diff_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "immo_diff.dat")
            if os.path.isfile(immo_diff_path):
                with open(immo_diff_path, 'r') as sent_file:
                    sent_ids = [line.strip() for line in sent_file if line.strip()]
                    blacklist.extend(sent_ids)

            # Manual blacklist
            manual_bl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "immo_blacklist.txt")
            if os.path.isfile(manual_bl_path):
                with open(manual_bl_path, 'r') as manual_file:
                    manual_ids = [line.strip() for line in manual_file if line.strip() and not line.startswith('#')]
                    blacklist.extend(manual_ids)

            print(f"Blacklist + historically sent: {len(blacklist)} items")

            # Find new offers not in blacklist
            diff_id = [eid for eid in data if eid not in blacklist]

            if len(diff_id) != 0:
                print(len(diff_id), "new offers found")
                print("New offers:", diff_id)
                print("Time: ", datetime.now())

                immo_sent_req_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "immo_sent_request.dat")

                for new in diff_id:
                    print("Sending message to expose:", new)
                    success = submit_immo.submit_app(driver, new)

                    # Only log as processed if message was actually sent
                    if success:
                        with open(immo_sent_req_path, "a") as text_file, open(immo_diff_path, "a") as text_file1:
                            text_file.write("ID: %s \n" % new)
                            text_file.write(str(datetime.now()) + '\n')
                            text_file1.write(str(new) + '\n')

                sleep_time = 0  # Skip sleep if we just processed new offers
            else:
                print("No new offers.")
                print("Time: ", datetime.now())
                sleep_time = CHECK_INTERVAL

        finally:
            driver.quit()  # Cleanly shut browser down after batch completes

    except JSONDecodeError as e:
        print("There was a problem with reading a json formatted object")
        print("".join(traceback.TracebackException.from_exception(e).format()))
        sleep_time = CHECK_INTERVAL
    except Exception as e:
        print(f"Unexpected error: {e}")
        print("".join(traceback.TracebackException.from_exception(e).format()))
        sleep_time = CHECK_INTERVAL
    finally:
        if sleep_time > 0:
            print("Sleeping for", sleep_time, "seconds...")
            time.sleep(sleep_time)
