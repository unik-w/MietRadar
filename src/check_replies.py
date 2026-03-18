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
from src.submit_wg import create_driver, random_sleep
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv(str(Path(__file__).parent.parent / "config" / ".env"))
WG_EMAIL = os.getenv("WG_EMAIL")
WG_PASSWORD = os.getenv("WG_PASSWORD")

def parse_wg_date(date_str: str) -> datetime:
    """
    Parse WG-Gesucht date strings like '06.03.2026', 'heute', 'gestern', or 'vor X Tagen'.
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
    driver.get("https://www.wg-gesucht.de/")
    random_sleep(2, 3)

    try:
        banners = driver.find_elements(By.XPATH, "//*[contains(text(),'Akzeptieren') or contains(text(),'Accept all') or contains(text(),'Alle akzeptieren')]")
        if banners:
            banners[0].click()
            random_sleep(1, 2)
    except Exception:
        pass

    page_src = driver.page_source.lower()
    if any(kw in page_src for kw in ["abmelden", "meine-anzeigen", "ausloggen"]):
        return
    if not WG_EMAIL or not WG_PASSWORD:
        return

    try:
        konto_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'Mein Konto')]")
        if konto_btns:
            konto_btns[0].click()
            wait = WebDriverWait(driver, 10)
            email_field = wait.until(EC.visibility_of_element_located((By.ID, "login_email_username")))
            email_field.clear()
            email_field.send_keys(WG_EMAIL)
            pwd_field = driver.find_element(By.ID, "login_password")
            pwd_field.clear()
            pwd_field.send_keys(WG_PASSWORD)
            driver.find_element(By.ID, "login_submit").click()
            random_sleep(3, 5)
    except Exception:
        pass


def get_sent_requests(dat_path):
    records = []
    if not os.path.exists(dat_path):
        return records

    with open(dat_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    for i in range(0, len(lines), 2):
        if i + 1 >= len(lines):
            break
        id_line = lines[i]
        time_line = lines[i+1]
        
        if id_line.startswith("ID: "):
            url = id_line[4:].strip()
            m = re.search(r'\.(\d{6,10})\.html', url)
            if not m:
                continue
            anzeigenummer = m.group(1)
            
            try:
                sent_time = datetime.strptime(time_line.strip(), "%Y-%m-%d %H:%M:%S.%f")
            except Exception:
                try:
                    sent_time = datetime.strptime(time_line.strip(), "%Y-%m-%d %H:%M:%S")
                except:
                    sent_time = datetime.now()
            
            records.append({
                "url": url,
                "anzeigenummer": anzeigenummer,
                "sent_time": sent_time,
            })
    return records


def load_previous_report(csv_path):
    """
    Load existing 🟢 (Replied) conversations from the CSV to avoid redundant checking.
    Returns a dict: { nachrichten_id: { 'anzeigenummer': str, 'name': str } }
    """
    cache = {}
    if not os.path.exists(csv_path):
        return cache

    try:
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get("Status") == "🟢" and row.get("Nachrichten-ID"):
                    cache[row["Nachrichten-ID"]] = {
                        "anzeigenummer": row["Anzeigenummer"],
                        "name": row["Name"]
                    }
    except Exception as e:
        print(f"⚠️ Could not load previous report cache: {e}")
    
    if cache:
        print(f"Loaded {len(cache)} already-replied conversations from cache.")
    return cache


def build_inbox_dictionary(driver, max_pages=10, green_cache=None, earliest_date=None):
    """
    Scrape the Posteingang (filter_type=0) inbox pages. 
    If a conversation is in green_cache, skip visiting its page.
    Filters out messages older than earliest_date.
    """
    print("\nLoading Posteingang (Inbox) to fetch conversations...")
    if earliest_date:
        # Normalize earliest_date to start of day for comparison
        threshold = earliest_date.replace(hour=0, minute=0, second=0, microsecond=0)
        print(f"Threshold date: {threshold.strftime('%Y-%m-%d')} (filtering out older messages)")
    else:
        threshold = None

    conversations = {}  # nachrichten_id -> { "href": str, "name": str, "title": str }
    green_cache = green_cache or {}
    
    stop_pagination = False
    for page in range(1, max_pages + 1):
        if stop_pagination: break
        
        url = f"https://www.wg-gesucht.de/nachrichten.html?filter_type=0&page={page}"
        driver.get(url)
        random_sleep(2, 4)
        print(f"Scanning Posteingang page {page}...")
        
        items = driver.find_elements(By.CSS_SELECTOR, "div.conversation_list_item")
        if not items:
            break
        
        page_found = 0
        for item in items:
            try:
                # ------------------------------------------------------
                # Date Check
                # ------------------------------------------------------
                if threshold:
                    date_els = item.find_elements(By.CSS_SELECTOR, "div.latest_message_timestamp_list")
                    if date_els:
                        raw_date_text = date_els[0].text.strip()
                        msg_date = parse_wg_date(raw_date_text)
                        
                        # Debug unparseable dates but don't stop the loop for them
                        if msg_date == datetime(2000, 1, 1):
                            print(f"  [DEBUG] Could not parse date format: '{raw_date_text}'")
                            continue

                        # Only stop if we genuinely found a confirmed old date
                        if msg_date < threshold:
                            print(f"  Reached messages from {msg_date.strftime('%Y-%m-%d')} (before bot started). Stopping.")
                            stop_pagination = True
                            break

                # ------------------------------------------------------
                # Scrape Conversation Data
                # ------------------------------------------------------
                links = item.find_elements(By.CSS_SELECTOR, "a.link-conversation-list[href*='nachrichten-id']")
                if not links: continue
                href = links[0].get_attribute("href")
                if not href: continue
                
                m = re.search(r'nachrichten-id=(\d+)', href)
                if not m: continue
                nachr_id = m.group(1)
                
                if nachr_id in conversations: continue
                
                name_els = item.find_elements(By.CSS_SELECTOR, "span.list_item_public_name")
                name = name_els[0].text.strip() if name_els else "Unknown"
                
                h3_els = item.find_elements(By.TAG_NAME, "h3")
                title = h3_els[0].text.strip() if h3_els else ""
                
                conversations[nachr_id] = {"href": href, "name": name, "title": title}
                page_found += 1
            except Exception:
                continue
        
        print(f"  Found {page_found} conversations on page {page}.")
        if page_found < 25 or stop_pagination:
            break
    
    total = len(conversations)
    print(f"\nCollected {total} relevant conversations from Posteingang.")
    
    inbox_map = {}  # anzeigenummer -> { "name": str, "replied": True, "nachr_id": str }
    
    for idx, (nachr_id, info) in enumerate(conversations.items(), 1):
        # 1. Check if we already have this in our "Replied" cache
        if nachr_id in green_cache:
            c_data = green_cache[nachr_id]
            inbox_map[c_data["anzeigenummer"]] = {
                "name": c_data["name"], 
                "replied": True, 
                "nachr_id": nachr_id
            }
            continue

        # 2. Otherwise, visit the page
        print(f"  [{idx}/{total}] Checking new conversation with {info['name']}...")
        driver.get(info["href"])
        random_sleep(0.3, 0.6)
        
        anzeigenummer = None
        page_src = None
        
        # Link check
        listing_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='wg-zimmer'], a[href*='wohnungen'], a[href*='haeuser'], a[href*='1-zimmer']")
        for lnk in listing_links:
            href = lnk.get_attribute("href")
            if href:
                m = re.search(r'\.(\d{6,10})\.html', href)
                if m:
                    anzeigenummer = m.group(1)
                    break
        
        # Fallback 1: deleted ad banner
        if not anzeigenummer:
            page_src = driver.page_source
            m = re.search(r'Die Anzeige mit der Nummer\s*(?:<[^>]+>)?\s*(\d{6,10})\s*(?:<[^>]+>)?\s*existiert nicht', page_src)
            if m:
                anzeigenummer = m.group(1)
        
        # Fallback 2: regex general
        if not anzeigenummer:
            if not page_src: page_src = driver.page_source
            m = re.search(r'\.(\d{6,10})\.html', page_src)
            if m: anzeigenummer = m.group(1)
        
        if anzeigenummer:
            if not page_src: page_src = driver.page_source
            name = info["name"]
            if "Anzeige wurde gelöscht" in page_src or "deaktiviert" in page_src:
                name = "Deactivated Ad / " + name
            
            inbox_map[anzeigenummer] = {"name": name, "replied": True, "nachr_id": nachr_id}
    
    return inbox_map


def check_replies():
    dat_path = Path(__file__).parent.parent / "data" / "wg_sent_request.dat"
    csv_path = Path(__file__).parent.parent / "data" / "wg_replies_report.csv"
    
    records = get_sent_requests(dat_path)
    print(f"Loaded {len(records)} sent requests from database.")
    if not records:
        print("No sent requests found. Exiting.")
        return

    # Find the earliest message ever sent to use as a global threshold
    earliest_date = min(r["sent_time"] for r in records)
    
    # Load cache of previous replies to speed up scanning
    green_cache = load_previous_report(csv_path)

    now = datetime.now()
    driver = create_driver()
    try:
        ensure_logged_in(driver)
        
        inbox_map = build_inbox_dictionary(driver, green_cache=green_cache, earliest_date=earliest_date)
        
        print(f"\nWriting updated report for {len(records)} records...")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Anzeigenummer", "Nachrichten-ID", "Name", "Status"])
            
            counts = {"🟢": 0, "🟡": 0, "🔴": 0}
            
            for rec in records:
                anzeigenummer = rec["anzeigenummer"]
                sent_time = rec["sent_time"]
                is_old = (now - sent_time).total_seconds() > 3 * 24 * 3600
                
                # Default values
                nachr_id = "-"
                
                if anzeigenummer in inbox_map:
                    chat_info = inbox_map[anzeigenummer]
                    name = chat_info["name"]
                    nachr_id = chat_info.get("nachr_id", "-")
                    status = "🟢"
                else:
                    name = "Unbekannt / Gelöscht"
                    status = "🔴" if is_old else "🟡"
                
                counts[status] += 1
                writer.writerow([anzeigenummer, nachr_id, name, status])
            
            # Summary
            writer.writerow([])
            writer.writerow(["SUMMARY", "", "Count", "Percentage"])
            total = len(records)
            for s, c in counts.items():
                pct = f"{(c/total)*100:.1f}%" if total > 0 else "0%"
                writer.writerow([s, "", c, pct])
                
        print(f"\n✅ All done! Report saved to {csv_path}")
    finally:
        driver.quit()

if __name__ == "__main__":
    check_replies()
