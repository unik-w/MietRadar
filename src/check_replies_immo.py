"""
check_replies_immo.py
=====================
Check which IS24 applications received replies.
Uses the same logic as check_replies.py (WG-Gesucht) but adapted for IS24's
messaging system.

Reads immo_sent_request.dat, logs into IS24, scans the messaging inbox,
and generates immo_replies_report.csv with 🟢/🟡/🔴 status indicators.
"""

import os
import sys
import time
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Add root to sys.path to allow imports from src/ if run directly
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from submit_immo import create_driver, solve_captcha_if_present, IMMO_EMAIL, IMMO_PASSWORD
from submit_wg import random_sleep
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv(str(Path(__file__).parent.parent / "config" / ".env"))


def parse_immo_date(date_str: str) -> datetime:
    """
    Parse IS24 date strings like '06.03.2026', 'heute', 'gestern', or 'vor X Tagen'.
    Returns a datetime object (midnight of that day).
    """
    date_str = date_str.lower().strip()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if "heute" in date_str or "stund" in date_str or "minut" in date_str:
        return today
    if "gestern" in date_str:
        return today - timedelta(days=1)

    # Handle "vor X Tagen"
    m_days = re.search(r'vor (\d+)\s+tagen', date_str)
    if m_days:
        return today - timedelta(days=int(m_days.group(1)))

    # Try DD.MM.YYYY
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', date_str)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    # Fallback to very old date if unparseable
    return datetime(2000, 1, 1)


def ensure_logged_in(driver) -> None:
    """Log into IS24 using the multi-step flow."""
    driver.get("https://www.immobilienscout24.de/")
    random_sleep(2, 3)

    # Cookie banner
    try:
        banners = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'Alle akzeptieren') or contains(text(),'Akzeptieren') or contains(text(),'Accept all')]"
        )
        if banners:
            banners[0].click()
            random_sleep(1, 2)
    except Exception:
        pass

    # Check if already logged in
    page_src = driver.page_source.lower()
    if any(kw in page_src for kw in ["abmelden", "meinkonto", "mein konto"]):
        print("  ✅ Already logged in.")
        return

    if not IMMO_EMAIL or not IMMO_PASSWORD:
        print("  ⚠️  No IMMO credentials — skipping login.")
        return

    # Navigate to login
    driver.get("https://www.immobilienscout24.de/geschlossenerbereich/start.html")
    random_sleep(2, 4)
    solve_captcha_if_present(driver)

    try:
        # Step 1: email
        wait = WebDriverWait(driver, 10)
        email_field = wait.until(EC.visibility_of_element_located((By.ID, "username")))
        email_field.clear()
        email_field.send_keys(IMMO_EMAIL)
        random_sleep(0.5, 1)

        submit_btn = driver.find_element(By.ID, "submit")
        submit_btn.click()
        random_sleep(3, 5)
        solve_captcha_if_present(driver)

        # Step 2: password
        pwd_field = wait.until(EC.visibility_of_element_located((By.ID, "password")))
        if not pwd_field:
            pwd_field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
        pwd_field.clear()
        pwd_field.send_keys(IMMO_PASSWORD)
        random_sleep(0.5, 1)

        try:
            login_btn = driver.find_element(By.ID, "loginOrRegistration")
        except Exception:
            try:
                login_btn = driver.find_element(By.ID, "submit")
            except Exception:
                login_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        login_btn.click()
        random_sleep(3, 5)
        solve_captcha_if_present(driver)
        print("  🔑 Logged in to IS24.")
    except Exception as e:
        print(f"  ⚠️ Login failed: {e}")


def get_sent_requests(dat_path):
    """Parse immo_sent_request.dat for sent application records."""
    records = []
    if not os.path.exists(dat_path):
        return records

    with open(dat_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    for i in range(0, len(lines), 2):
        if i + 1 >= len(lines):
            break
        id_line = lines[i]
        time_line = lines[i + 1]

        if id_line.startswith("ID: "):
            expose_id = id_line[4:].strip()

            try:
                sent_time = datetime.strptime(time_line.strip(), "%Y-%m-%d %H:%M:%S.%f")
            except Exception:
                try:
                    sent_time = datetime.strptime(time_line.strip(), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    sent_time = datetime.now()

            records.append({
                "expose_id": expose_id,
                "sent_time": sent_time,
            })
    return records


def load_previous_report(csv_path):
    """
    Load existing 🟢 (Replied) conversations from the CSV to avoid redundant checking.
    Returns a dict: { expose_id: { 'name': str } }
    """
    cache = {}
    if not os.path.exists(csv_path):
        return cache

    try:
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get("Status") == "🟢" and row.get("Expose-ID"):
                    cache[row["Expose-ID"]] = {
                        "name": row.get("Name", "Unknown"),
                    }
    except Exception as e:
        print(f"⚠️ Could not load previous report cache: {e}")

    if cache:
        print(f"Loaded {len(cache)} already-replied conversations from cache.")
    return cache


def build_inbox_dictionary(driver, max_pages=10, green_cache=None, earliest_date=None):
    """
    Scrape the IS24 messaging inbox.
    Returns a dict: expose_id -> { "name": str, "replied": True }
    """
    print("\nLoading IS24 messaging inbox to check for replies...")
    if earliest_date:
        threshold = earliest_date.replace(hour=0, minute=0, second=0, microsecond=0)
        print(f"Threshold date: {threshold.strftime('%Y-%m-%d')} (filtering out older messages)")
    else:
        threshold = None

    green_cache = green_cache or {}
    inbox_map = {}

    # Navigate to IS24 messaging inbox
    for page in range(1, max_pages + 1):
        inbox_url = f"https://www.immobilienscout24.de/meinkonto/nachrichten/empfangen?page={page}"
        driver.get(inbox_url)
        random_sleep(2, 4)
        solve_captcha_if_present(driver)
        print(f"Scanning inbox page {page}...")

        # Find conversation list items
        items = driver.find_elements(By.CSS_SELECTOR,
            "div.message-list-item, tr.message-list-item, li.message-list-item, "
            "[class*='conversation'], [class*='message-item'], [class*='MessageListItem']"
        )

        if not items:
            # Fallback: try broader selectors
            items = driver.find_elements(By.CSS_SELECTOR,
                "a[href*='nachrichten'], a[href*='conversation']"
            )

        if not items:
            print(f"  No conversations found on page {page}.")
            break

        page_found = 0
        for item in items:
            try:
                # Try to extract expose ID from the conversation link or content
                item_html = item.get_attribute("outerHTML") or ""

                # Look for expose ID references
                expose_match = re.search(r'/expose/(\d+)', item_html)
                if not expose_match:
                    expose_match = re.search(r'expose[_-]?id["\s:=]+(\d+)', item_html, re.IGNORECASE)

                if not expose_match:
                    # Try to find in link text or conversation text
                    try:
                        links = item.find_elements(By.CSS_SELECTOR, "a[href*='expose']")
                        for lnk in links:
                            href = lnk.get_attribute("href") or ""
                            m = re.search(r'/expose/(\d+)', href)
                            if m:
                                expose_match = m
                                break
                    except Exception:
                        pass

                if expose_match:
                    expose_id = expose_match.group(1)

                    # Check green cache
                    if expose_id in green_cache:
                        inbox_map[expose_id] = {
                            "name": green_cache[expose_id]["name"],
                            "replied": True,
                        }
                        page_found += 1
                        continue

                    # Get sender name
                    name = "Unknown"
                    try:
                        name_els = item.find_elements(By.CSS_SELECTOR,
                            ".sender-name, .contact-name, .name, strong, b"
                        )
                        if name_els:
                            name = name_els[0].text.strip() or "Unknown"
                    except Exception:
                        pass

                    inbox_map[expose_id] = {"name": name, "replied": True}
                    page_found += 1
            except Exception:
                continue

        print(f"  Found {page_found} conversations on page {page}.")
        if page_found < 20:
            break

    print(f"\nCollected {len(inbox_map)} conversations from inbox.")
    return inbox_map


def check_replies():
    """Main entry point: check which IS24 applications received replies."""
    dat_path = Path(__file__).parent.parent / "data" / "immo_sent_request.dat"
    csv_path = Path(__file__).parent.parent / "data" / "immo_replies_report.csv"

    records = get_sent_requests(dat_path)
    print(f"Loaded {len(records)} sent requests from database.")
    if not records:
        print("No sent requests found. Exiting.")
        return

    # Find the earliest message ever sent
    earliest_date = min(r["sent_time"] for r in records)

    # Load cache of previous replies
    green_cache = load_previous_report(csv_path)

    now = datetime.now()
    driver = create_driver()
    try:
        ensure_logged_in(driver)

        inbox_map = build_inbox_dictionary(driver, green_cache=green_cache, earliest_date=earliest_date)

        print(f"\nWriting updated report for {len(records)} records...")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Expose-ID", "Name", "Status"])

            counts = {"🟢": 0, "🟡": 0, "🔴": 0}

            for rec in records:
                expose_id = rec["expose_id"]
                sent_time = rec["sent_time"]
                is_old = (now - sent_time).total_seconds() > 3 * 24 * 3600

                if expose_id in inbox_map:
                    chat_info = inbox_map[expose_id]
                    name = chat_info["name"]
                    status = "🟢"
                else:
                    name = "Unbekannt / Keine Antwort"
                    status = "🔴" if is_old else "🟡"

                counts[status] += 1
                writer.writerow([expose_id, name, status])

            # Summary
            writer.writerow([])
            writer.writerow(["SUMMARY", "Count", "Percentage"])
            total = len(records)
            for s, c in counts.items():
                pct = f"{(c / total) * 100:.1f}%" if total > 0 else "0%"
                writer.writerow([s, c, pct])

        print(f"\n✅ All done! Report saved to {csv_path}")
    finally:
        driver.quit()


if __name__ == "__main__":
    check_replies()
