import time
import os
from dotenv import load_dotenv
import submit_wg
from selenium.webdriver.common.by import By

load_dotenv()
url = os.getenv("WG_SEARCH_URLS")

driver = submit_wg.create_driver()
driver.get(url)
submit_wg.random_sleep(3, 5)
submit_wg.solve_captcha_if_present(driver)

try:
    banners = driver.find_elements(By.XPATH, "//*[contains(text(),'Akzeptieren') or contains(text(),'Accept all')]")
    if banners:
        submit_wg.human_move_and_click(driver, banners[0])
        submit_wg.random_sleep(1, 2)
except Exception:
    pass

links = set()
for p in range(3):
    print(f"Page {p+1}...")
    cards = driver.find_elements(By.CSS_SELECTOR, '.wgg_card')
    for card in cards:
        try:
            agency_text = card.text.lower()
            if not any(x in agency_text for x in ["housinganywhere", "spacest", "medici", "spotahome", "uniplaces"]):
                a = card.find_element(By.CSS_SELECTOR, 'h2.truncate_title a')
                link = a.get_attribute('href')
                if link and "/wg-zimmer-" in link or "/wohnungen-" in link or "/haeuser-" in link:
                    links.add(link)
        except Exception as e:
            pass
    print("Links so far:", len(links))
    
    # Try click next
    try:
        # Looking for '»' or 'Weiter' or the next number
        next_btn = driver.find_element(By.XPATH, "//div[@id='assets_list_pagination']//li[@class='active']/following-sibling::li[1]/a")
        submit_wg.human_move_and_click(driver, next_btn)
        time.sleep(3)
    except Exception as e:
        print("No next button found or error", e)
        break

print(f"Finished scraping {len(links)} valid links")
driver.quit()
