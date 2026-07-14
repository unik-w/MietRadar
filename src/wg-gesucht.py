import json
import os
import os.path
import time
import traceback
from datetime import datetime
from json import JSONDecodeError
from subprocess import call

from selenium.webdriver.common.by import By
from dotenv import load_dotenv

import submit_wg

# Load the environment from the config folder relative to this file
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env")
load_dotenv(dotenv_path=env_path)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))
fname = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "wg_offer.json")


def click_next_page(driver):
    """
    Click the real "next page" (>) arrow in the results pagination widget.

    IMPORTANT: Navigating directly to a page-N URL via driver.get() does NOT
    work — WG-Gesucht's server redirects any such direct/full-navigation
    request back to the canonical page-1 URL (confirmed by testing: the
    resulting driver.current_url always lands back on "...1.0.html" even
    though the address bar shows "...1.1.html"). The only reliable way to
    reach subsequent pages is to click the actual in-page pagination link,
    which carries the correct Referer/session context.

    Returns True if a next-page link was found and clicked, False otherwise
    (i.e. we're on the last page).
    """
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "#main_column a.black_text_link.mr5")
        next_link = next((l for l in candidates if l.text.strip() == ">"), None)
        if next_link is None:
            return False
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_link)
        submit_wg.random_sleep(0.3, 0.8)
        driver.execute_script("arguments[0].click();", next_link)
        submit_wg.random_sleep(2, 4)
        return True
    except Exception:
        return False


def scrape_site(driver=None):
    close_driver = False
    if driver is None:
        driver = submit_wg.create_driver()
        close_driver = True

    url = os.getenv("WG_SEARCH_URLS")
    driver.get(url)
    submit_wg.random_sleep(3, 5)

    # Dismiss security/cookie banners
    try:
        banners = driver.find_elements(By.XPATH, "//*[contains(text(),'Akzeptieren') or contains(text(),'Accept all')]")
        if banners:
            submit_wg.human_move_and_click(driver, banners[0])
            submit_wg.random_sleep(1, 2)
    except Exception:
        pass

    links = set()
    pages_to_scrape = 10
    
    print(f"\n🔍 Scraping up to {pages_to_scrape} pages of WG-Gesucht...")
    
    for page in range(1, pages_to_scrape + 1):
        print(f"  -> Scanning Page {page}...")

        cards = driver.find_elements(By.CSS_SELECTOR, '.wgg_card')
        if not cards:
            print("  -> No listings found on this page, stopping.")
            break

        for card in cards:
            try:
                card_text = card.text.lower()
                # Exclude commercial platforms immediately
                if not any(x in card_text for x in ["housinganywhere", "spacest", "medici", "spotahome", "uniplaces"]):
                    a = card.find_element(By.CSS_SELECTOR, 'h2.truncate_title a')
                    link = a.get_attribute('href')
                    if link and ("wg-zimmer" in link or "wohnungen" in link or "haeuser" in link):
                        # Convert absolute URL safely to relative data-id
                        link_rel = link.replace("https://www.wg-gesucht.de", "") 
                        links.add(link_rel)
            except Exception:
                pass

        if page < pages_to_scrape:
            if not click_next_page(driver):
                print("  -> Reached last page or couldn't find Next button.")
                break

    data = list(links)
    
    # Save current findings for reference exactly like scrapy did
    with open(fname, 'w') as f:
        json.dump([{"data-id": d} for d in data], f)

    if close_driver:
        driver.quit()

    return data


while True:
    try:
        sleep_time = CHECK_INTERVAL
        
        # Read the environment / logic loop
        driver = submit_wg.init_driver_and_login()
        
        try:
            if os.path.isfile(fname):
                print("'wg_offer.json' file found.")
            else:
                print("No 'wg_offer.json' file found.")
                
            data = scrape_site(driver=driver)

            blacklist = []
            
            # Already sent list (wg_diff.dat)
            # Prevents re-opening Chrome for listings we've successfully messaged in the past
            wg_diff_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "wg_diff.dat")
            if os.path.isfile(wg_diff_path):
                with open(wg_diff_path, 'r') as sent_file:
                    sent_ids = [line.strip() for line in sent_file if line.strip()]
                    blacklist.extend(sent_ids)
                    
            # User's manual text-based blacklist
            manual_bl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "wg_blacklist.txt")
            if os.path.isfile(manual_bl_path):
                with open(manual_bl_path, 'r') as manual_file:
                    # Strip out whitespace and ignore commented/empty lines
                    manual_ids = [line.strip() for line in manual_file if line.strip() and not line.startswith('#')]
                    blacklist.extend(manual_ids)
            
            print(f"Blacklist + historically sent: {len(blacklist)} items")

            # Check new findings against the blacklist (both exact URL and just ID)
            diff_id = []
            for item in data:
                try:
                    # Item looks like /wg-zimmer-in-Muenchen...12345.html
                    item_id = item.split('.')[-2]
                except Exception:
                    item_id = None
                    
                if item not in blacklist and item_id not in blacklist:
                    diff_id.append(item)
            if len(diff_id) != 0:
                print(len(diff_id), "new offers found")
                print("New offers id:", diff_id)
                print("Time: ", datetime.now())
                
                for new in diff_id:
                    print("Sending message to: ", new)
                    submit_wg.submit_app(driver, new)
                    
                    # Log as processed instantly, so if we crash or skip (e.g. HousingAnywhere), 
                    # we don't ever revisit this ID in the future.
                    wg_sent_req_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "wg_sent_request.dat")
                    with open(wg_sent_req_path, "a") as text_file, open(wg_diff_path, "a") as text_file1:
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
