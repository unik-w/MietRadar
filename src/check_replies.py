import os
import sys
import time
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Add root to sys.path to allow imports from src/ if run directly
# Since we are already in src/, we just need to make sure we can import peer modules.
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.submit_wg import create_driver, random_sleep
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv(str(Path(__file__).parent.parent / "config" / ".env"))
WG_EMAIL = os.getenv("WG_EMAIL")
WG_PASSWORD = os.getenv("WG_PASSWORD")

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
            # Extract anzeigenummer from wg_sent_request url
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


def build_inbox_dictionary(driver):
    print("\nLoading Inbox (Nachrichten) to fetch all conversations...")
    driver.get("https://www.wg-gesucht.de/nachrichten.html")
    random_sleep(3, 5)
    
    grouped = {}
    
    # Scrape up to 10 pages in the inbox
    for page in range(1, 11):
        chat_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'nachricht.html?nachrichten-id')]")
        
        for link in chat_links:
            href = link.get_attribute("href")
            txt = link.text.strip()
            if not href or not txt:
                continue
            if href not in grouped:
                grouped[href] = []
            if txt not in grouped[href]:
                grouped[href].append(txt)
                
        # Try to advance to the next page
        next_page = page + 1
        try:
            btn = driver.find_element(By.XPATH, f"//a[@href='#page-{next_page}']")
            driver.execute_script("arguments[0].scrollIntoView(true);", btn)
            random_sleep(0.5, 1)
            driver.execute_script("arguments[0].click();", btn)
            print(f"Loading inbox page {next_page}...")
            random_sleep(2, 4)
        except Exception:
            # End of pagination
            break
            
            
    convo_data = []
    for href, texts in grouped.items():
        if len(texts) >= 3:
            name = texts[1] if len(texts[0]) <= 2 else texts[0]
            convo_data.append((href, name))
        elif len(texts) == 2:
            name = texts[0]
            convo_data.append((href, name))
        else:
            convo_data.append((href, texts[0] if texts else "Unknown"))
            
    inbox_map = {}
    total = len(convo_data)
    print(f"Found {total} unique conversation active links. Fast-mapping IDs...")
    
    for idx, (href, name) in enumerate(convo_data, 1):
        driver.get(href)
        random_sleep(0.5, 1.0)
        
        # 1. Look for Anzeigenummer link on the page
        links = driver.find_elements(By.XPATH, "//a[contains(@href, '.html')]")
        anzeigenummer = None
        for a in links:
            l_href = a.get_attribute("href")
            if l_href:
                m = re.search(r'\.(\d{6,10})\.html', l_href)
                if m:
                    anzeigenummer = m.group(1)
                    break 
                    
        
        # 2. Check for replies from the poster
        # WG-Gesucht uses `.message` for chat bubbles, and `.my_message` specifically for the user's messages.
        replied = False
        try:
            chat_bubbles = driver.find_elements(By.CSS_SELECTOR, ".message")
            for bubble in chat_bubbles:
                classes = bubble.get_attribute("class") or ""
                # If there's a message block that is NOT ours, they replied!
                if "my_message" not in classes:
                    replied = True
                    break
        except Exception:
            pass
            
        page_src = driver.page_source
        if "Anzeige wurde gelöscht" in page_src or "deaktiviert" in page_src:
            name = "Deactivated Ad / " + name
            
        if anzeigenummer:
            inbox_map[anzeigenummer] = {"name": name, "replied": replied}
            
    return inbox_map


def check_replies():
    dat_path = Path(__file__).parent.parent / "data" / "wg_sent_request.dat"
    csv_path = Path(__file__).parent.parent / "data" / "wg_replies_report.csv"
    
    records = get_sent_requests(dat_path)
    print(f"Loaded {len(records)} sent requests from database.")
    if not records:
        print("No sent requests found. Exiting.")
        return

    now = datetime.now()
    driver = create_driver()
    try:
        ensure_logged_in(driver)
        
        inbox_map = build_inbox_dictionary(driver)
        
        print(f"\nWriting final report for {len(records)} records...")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Anzeigenummer", "Name", "Status"])
            
            # Counters for summary
            counts = {"🟢": 0, "🟡": 0, "🔴": 0}
            
            for rec in records:
                anzeigenummer = rec["anzeigenummer"]
                sent_time = rec["sent_time"]
                is_old = (now - sent_time).total_seconds() > 3 * 24 * 3600
                
                if anzeigenummer in inbox_map:
                    chat_info = inbox_map[anzeigenummer]
                    name = chat_info["name"]
                    if chat_info["replied"]:
                        status = "🟢"
                    else:
                        status = "🔴" if is_old else "🟡"
                else:
                    name = "Unbekannt / Gelöscht"
                    status = "🔴" if is_old else "🟡"
                
                counts[status] += 1
                writer.writerow([anzeigenummer, name, status])
            
            # Add summary breakdown at the bottom
            writer.writerow([])
            writer.writerow(["SUMMARY", "Count", "Percentage"])
            total = len(records)
            for s, c in counts.items():
                pct = f"{(c/total)*100:.1f}%" if total > 0 else "0%"
                writer.writerow([s, c, pct])
                
        print(f"\n✅ All done! Report saved to {csv_path}")
    finally:
        driver.quit()

if __name__ == "__main__":
    check_replies()
