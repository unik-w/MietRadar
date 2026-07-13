# -*- coding: utf-8 -*-
"""
submit_immo.py
==============
ImmobilienScout24 automation module — stealth Chrome driver, login,
listing info extraction, LLM personalisation, and application submission.

Mirrors the architecture of submit_wg.py but adapted for IS24's
multi-step login flow and expose/contact-form structure.

Shares common helpers (random_sleep, human_type, human_move_and_click,
wait_for) and config files (message.txt, llm_persona.txt) with submit_wg.py.
"""

import os
import re
import time
import random
import traceback

from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Load credentials from .env file inside config/
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env")
load_dotenv(dotenv_path=env_path)

# ── Shared helpers + LLM state from submit_wg (single source of truth) ──────
# submit_wg already handles LLM init and prints the banner once.
# Re-importing from there avoids printing it a second time.
import submit_wg as _submit_wg_mod
from submit_wg import random_sleep, human_type, human_move_and_click, wait_for

_USE_LLM = getattr(_submit_wg_mod, '_USE_LLM', False)
_llm_personalise = getattr(_submit_wg_mod, '_llm_personalise', None)
_llm_model = getattr(_submit_wg_mod, '_llm_model', 'the configured LLM')

IMMO_EMAIL = os.getenv("IMMO_EMAIL")
IMMO_PASSWORD = os.getenv("IMMO_PASSWORD")

if not IMMO_EMAIL or not IMMO_PASSWORD:
    raise ValueError(
        "IMMO_EMAIL and IMMO_PASSWORD must be set in your .env file. "
        "Copy .env.example to .env and fill in your credentials."
    )

CAPTCHA_WAIT_SECONDS = 120  # How long to wait for manual CAPTCHA solving

# Personal details for the contact form
IMMO_SALUTATION = os.getenv("IMMO_SALUTATION", "Herr")
IMMO_FIRST_NAME = os.getenv("IMMO_FIRST_NAME", "")
IMMO_LAST_NAME = os.getenv("IMMO_LAST_NAME", "")
IMMO_PHONE = os.getenv("IMMO_PHONE", "")
IMMO_STREET = os.getenv("IMMO_STREET", "")
IMMO_HOUSE_NUMBER = os.getenv("IMMO_HOUSE_NUMBER", "")
IMMO_POSTCODE = os.getenv("IMMO_POSTCODE", "")
IMMO_CITY = os.getenv("IMMO_CITY", "")


def solve_captcha_if_present(driver):
    """
    Detect IS24's "Ich bin kein Roboter" / "Gleich geht's weiter" challenge.
    Also handles reCAPTCHA / hCaptcha iframes.
    """
    # Check for IS24's custom challenge page
    try:
        page_title = driver.title.lower() if driver.title else ""
        page_src_lower = driver.page_source[:2000].lower()
        is_challenge = (
            "ich bin kein roboter" in page_title
            or "gleich geht" in page_src_lower
            or "ich bin kein roboter" in page_src_lower
        )
    except Exception:
        is_challenge = False

    def is_visible(iframe):
        try:
            return iframe.is_displayed() and iframe.size.get("height", 0) > 10
        except Exception:
            return False

    recaptcha = [f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']") if is_visible(f)]
    hcaptcha = [f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha']") if is_visible(f)]

    if not (recaptcha or hcaptcha or is_challenge):
        return  # No challenge detected

    print(f"  ⚠️  CAPTCHA / Bot challenge detected! Please solve it in the browser within {CAPTCHA_WAIT_SECONDS}s…")
    deadline = time.time() + CAPTCHA_WAIT_SECONDS
    while time.time() < deadline:
        try:
            page_title = driver.title.lower() if driver.title else ""
            page_src_check = driver.page_source[:2000].lower()
            still_challenge = (
                "ich bin kein roboter" in page_title
                or "gleich geht" in page_src_check
            )
        except Exception:
            still_challenge = False

        still_visible = [
            f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha'], iframe[src*='hcaptcha']")
            if is_visible(f)
        ]
        if not still_visible and not still_challenge:
            print("  ✅ CAPTCHA solved. Continuing…")
            return
        time.sleep(2)
    print("  ⚠️  CAPTCHA wait timed out — continuing anyway.")


def create_driver():
    """
    Create a stealth Chrome instance using selenium + webdriver-manager.
    Uses a separate profile directory (immobot_profile) from the WG bot.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()

    # --- Remove Selenium/automation fingerprints ---
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    # --- Stability flags ---
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-extensions")
    options.add_argument("--lang=de-DE")
    options.add_argument("--window-size=1280,900")

    # Separate profile directory from WG bot
    profile_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "data", "immobot_profile"
    ))
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # --- JS stealth patches ---
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
        """
    })

    return driver


# ---------------------------------------------------------------------------
# IS24 Login (multi-step flow)
# ---------------------------------------------------------------------------

def init_driver_and_login():
    """
    Launch Chrome, navigate to IS24, handle cookies, and log in.
    IS24 uses a two-step login: email first, then password on a second screen.
    Returns the ready-to-use driver instance.
    """
    driver = create_driver()

    try:
        # ── 1. Open homepage and handle cookie banner ────────────────────────
        print(f"\n🌐 Opening ImmobilienScout24 homepage to establish session…")
        driver.get("https://www.immobilienscout24.de/")
        random_sleep(2, 4)

        # Dismiss Chrome's "Restore pages?" bubble if present
        try:
            restore_close = driver.find_elements(
                By.XPATH, "//button[contains(.,'Don') or contains(.,'Nicht')]"
            )
            if restore_close:
                human_move_and_click(driver, restore_close[0])
                random_sleep(0.5, 1)
        except Exception:
            pass

        # Cookie banner
        try:
            cookie_btns = driver.find_elements(
                By.XPATH,
                "//*[contains(text(),'Alle akzeptieren') or contains(text(),'Akzeptieren') or contains(text(),'Accept all')]"
            )
            if cookie_btns:
                human_move_and_click(driver, cookie_btns[0])
                random_sleep(1, 2)
                print("  🍪 Cookie/consent banner accepted.")
        except Exception:
            pass

        solve_captcha_if_present(driver)

        # ── 2. Navigate to login page ────────────────────────────────────────
        print("  🔑 Navigating to login page…")
        driver.get("https://www.immobilienscout24.de/geschlossenerbereich/start.html")
        random_sleep(2, 4)
        solve_captcha_if_present(driver)

        # ── 3. Step 1: Enter email/username ──────────────────────────────────
        try:
            email_field = wait_for(driver, By.ID, "username", timeout=10)
            if email_field:
                human_type(email_field, IMMO_EMAIL)
                random_sleep(0.5, 1.2)

                # Click the submit/continue button
                submit_btn = driver.find_element(By.ID, "submit")
                human_move_and_click(driver, submit_btn)
                random_sleep(3, 5)
                solve_captcha_if_present(driver)

                # ── 4. Step 2: Enter password ────────────────────────────────
                pwd_field = wait_for(driver, By.ID, "password", timeout=10)
                if not pwd_field:
                    # Try alternative password field selectors
                    pwd_field = wait_for(driver, By.CSS_SELECTOR, "input[type='password']", timeout=5)

                if pwd_field:
                    human_type(pwd_field, IMMO_PASSWORD)
                    random_sleep(0.5, 1.5)

                    # Click login submit
                    try:
                        login_btn = driver.find_element(By.ID, "loginOrRegistration")
                    except NoSuchElementException:
                        try:
                            login_btn = driver.find_element(By.ID, "submit")
                        except NoSuchElementException:
                            login_btn = driver.find_element(
                                By.XPATH,
                                "//button[@type='submit' or contains(.,'Einloggen') or contains(.,'Anmelden')]"
                            )

                    human_move_and_click(driver, login_btn)
                    random_sleep(3, 5)
                    solve_captcha_if_present(driver)
                    print("  🔑 Logged in successfully.")
                else:
                    print("  ⚠️ Password field not found after email step.")
            else:
                print("  ⚠️ Email/username field not found on login page.")
        except Exception as e:
            print(f"  ⚠️ Login step error: {e}")

        # Verify logged in
        page_src = driver.page_source.lower()
        logged_in = any(kw in page_src for kw in ["abmelden", "mein konto", "logout", "meinkonto"])
        if not logged_in:
            print(f"  ⚠️  Login may have failed. Current URL: {driver.current_url}")
            print("     Proceeding anyway (session cookie may still work).")

    except Exception as e:
        print(f"  ❌ Critical login error: {e}")

    return driver


# ---------------------------------------------------------------------------
# Listing info extraction
# ---------------------------------------------------------------------------

def extract_listing_info(driver, expose_url):
    """
    Navigate to an IS24 expose page and extract description and poster info.
    Returns dict with 'description' and 'poster_name'.
    """
    info = {"description": "", "poster_name": ""}

    try:
        driver.get(expose_url)
        random_sleep(2, 4)
        solve_captcha_if_present(driver)

        # -- Expand all "weiterlesen..." links to get full text --
        try:
            weiterlesen = driver.find_elements(By.XPATH,
                "//a[contains(text(),'weiterlesen') or contains(text(),'Weiterlesen') or contains(@class,'weiterlesen')]"
            )
            for link in weiterlesen:
                try:
                    driver.execute_script("arguments[0].click();", link)
                    random_sleep(0.3, 0.5)
                except Exception:
                    pass
        except Exception:
            pass

        # -- Extract description from each named section --
        # Real IS24 structure (from DOM inspection):
        #   <h4 class="is24qa-objektbeschreibung-label ...">Objektbeschreibung</h4>
        #   <div class="is24-text margin-bottom is24-long-text-attribute">...</div>
        desc_parts = []

        SECTIONS = [
            ("h4.is24qa-objektbeschreibung-label", "Objektbeschreibung"),
            ("h4.is24qa-ausstattung-label",        "Ausstattung"),
            ("h4.is24qa-lage-label",               "Lage"),
            ("h4.is24qa-sonstiges-label",          "Sonstiges"),
        ]

        for header_sel, section_name in SECTIONS:
            try:
                header = driver.find_element(By.CSS_SELECTOR, header_sel)
                # Content is the immediately following sibling div
                content_div = header.find_element(By.XPATH, "following-sibling::div[1]")
                text = content_div.text.strip()
                if text and len(text) > 15:
                    desc_parts.append(f"--- {section_name} ---\n{text}")
            except NoSuchElementException:
                continue
            except Exception:
                continue

        # Fallback: any div with is24-long-text-attribute class
        if not desc_parts:
            try:
                blocks = driver.find_elements(By.CSS_SELECTOR, "div.is24-long-text-attribute")
                for block in blocks:
                    text = block.text.strip()
                    if text and len(text) > 30:
                        desc_parts.append(text)
            except Exception:
                pass

        info["description"] = "\n\n".join(desc_parts)

        # -- Extract poster/owner name --
        # Primary: IS24's stable data-qa attribute (most reliable)
        name_selectors = [
            'div[data-qa="contactName"]',       # IS24's stable test attribute
            ".is24qa-name-des-kontakts",
            ".contactDetails .contactName",
            ".realtor-info .name",
        ]
        for sel in name_selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                raw = el.text.strip()
                if raw and raw.lower() != "nachricht":
                    # Keep full name with Herr/Frau prefix
                    info["poster_name"] = raw
                    break
            except NoSuchElementException:
                continue

        # Fallback: look for name near the "Nachricht" / send button
        if not info["poster_name"]:
            try:
                # The name sits as a sibling/parent of the sendButton
                send_btn = driver.find_element(By.CSS_SELECTOR, 'button[data-qa="sendButton"]')
                parent = send_btn.find_element(By.XPATH, "./..")
                siblings = parent.find_elements(By.XPATH, "./preceding-sibling::*")
                for sib in siblings:
                    raw = sib.text.strip()
                    if raw and len(raw) >= 3 and raw.lower() != "nachricht":
                        # Avoid picking up other UI text
                        if raw[0].isupper() and not any(kw in raw.lower() for kw in ["telefon", "email", "adresse", "anbieter"]):
                            info["poster_name"] = raw
                            break
            except Exception:
                pass

    except Exception as e:
        print(f"  ⚠️ Error extracting listing info: {e}")

    return info


# ---------------------------------------------------------------------------
# Main submit function
# ---------------------------------------------------------------------------

def submit_app(driver, expose_id):
    """
    Given an already logged-in driver and an expose ID,
    navigate to the listing, fill in the contact form, and send.
    Returns True if message was sent successfully, False otherwise.
    """
    expose_url = f"https://www.immobilienscout24.de/expose/{expose_id}"

    try:
        # ── 1. Extract listing info for LLM personalisation ──────────────────
        print(f"\n📋 Extracting listing info for expose: {expose_id}")
        listing_info = extract_listing_info(driver, expose_url)
        poster_name = listing_info.get("poster_name", "")
        description = listing_info.get("description", "")

        print(f"  ✓ Poster name: {poster_name or '(not found)'}")
        print(f"  ✓ Description length: {len(description)} chars")

        # Skip apartment swap / Tauschwohnung / WohnungSwap listings
        skip_words = ["tauschwohnung", "wohnungstausch", "wohnungswap", "wohnungsswap"]
        combined_text = f"{poster_name} {description}".lower()
        if any(kw in combined_text for kw in skip_words):
            print(f"  ⏭  Skipping swap listing (Tauschwohnung): {expose_id}")
            return False

        # ── 2. We're already on the expose page (extract_listing_info navigated) ─
        # Just scroll back up so the contact form area is visible
        driver.execute_script("window.scrollTo(0, 0);")
        random_sleep(1, 2)

        # ── 3. Click "Nachricht schreiben" / contact button ──────────────────
        try:
            contact_btn = None
            for selector in [
                "//button[contains(.,'Nachricht')]",
                "//a[contains(.,'Nachricht')]",
                "//button[contains(.,'Kontakt')]",
                "//a[contains(.,'Kontakt')]",
                "//button[contains(@class,'button-primary')]",
            ]:
                try:
                    btns = driver.find_elements(By.XPATH, selector)
                    for btn in btns:
                        if btn.is_displayed():
                            contact_btn = btn
                            break
                    if contact_btn:
                        break
                except Exception:
                    continue

            if contact_btn:
                human_move_and_click(driver, contact_btn)
                random_sleep(2, 3)
                solve_captcha_if_present(driver)
                print("  ✉ Contact form opened.")
            else:
                print("  ⚠️ No contact button found — form may already be visible.")
        except Exception as e:
            print(f"  ⚠️ Contact button click error: {e}")

        # ── 4. Start LLM in background + fill form fields in parallel ────────
        # Read template early so the LLM thread can use it
        message_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "message.txt"
        )
        with open(message_path, "r", encoding="utf-8") as f:
            template = f.read()

        # Start LLM generation in a background thread (runs on GPU, doesn't block Selenium)
        from concurrent.futures import ThreadPoolExecutor, Future
        llm_future = None

        if _USE_LLM and description.strip():
            print(f"  🧠 Personalising message with {_llm_model} (background)…")
            executor = ThreadPoolExecutor(max_workers=1)
            llm_future = executor.submit(
                _llm_personalise,
                template=template,
                listing_description=description,
                poster_name=poster_name,
                listing_url=expose_url,
            )

        # ── Fill form fields while LLM runs ──────────────────────────────────
        # Salutation dropdown
        try:
            sal_dropdown = driver.find_element(By.ID, "salutation")
            if not sal_dropdown:
                sal_dropdown = driver.find_element(By.CSS_SELECTOR, "select[name*='salutation'], #contactForm-salutation")
            for option in sal_dropdown.find_elements(By.TAG_NAME, "option"):
                if IMMO_SALUTATION.lower() in option.text.lower():
                    option.click()
                    break
            random_sleep(0.3, 0.6)
        except NoSuchElementException:
            pass

        # Use human_type for form fields (runs while LLM generates in background)
        _fill_field_human(driver, ["#firstName", "#contactForm-firstName", "input[name*='firstName']"], IMMO_FIRST_NAME)
        _fill_field_human(driver, ["#lastName", "#contactForm-lastName", "input[name*='lastName']"], IMMO_LAST_NAME)
        _fill_field_human(driver, ["#emailAddress", "#contactForm-emailAddress", "input[name*='email']"], IMMO_EMAIL)
        if IMMO_PHONE:
            _fill_field_human(driver, ["#phoneNumber", "#contactForm-phoneNumber", "input[name*='phone']"], IMMO_PHONE)
        _fill_field_human(driver, ["#street", "#contactForm-street", "input[name*='street']"], IMMO_STREET)
        _fill_field_human(driver, ["#houseNumber", "#contactForm-houseNumber", "input[name*='houseNumber']"], IMMO_HOUSE_NUMBER)
        _fill_field_human(driver, ["#postcode", "#contactForm-postcode", "input[name*='postcode']"], IMMO_POSTCODE)
        _fill_field_human(driver, ["#city", "#contactForm-city", "input[name*='city']"], IMMO_CITY)

        # ── 5. Wait for LLM result + paste message ───────────────────────────
        text_area = None
        for sel in ["#message", "#contactForm-Message", "textarea[name*='message']", "textarea"]:
            try:
                text_area = driver.find_element(By.CSS_SELECTOR, sel)
                if text_area.is_displayed():
                    break
                text_area = None
            except NoSuchElementException:
                continue

        if not text_area:
            print(f"  ❌ Message textarea not found! Current URL: {driver.current_url}")
            return False

        if llm_future is not None:
            # Wait for LLM to finish (it's been running while we filled the form)
            message = llm_future.result(timeout=120)
            executor.shutdown(wait=False)
            print("  ✅ LLM finished.")
        else:
            # Plain substitution (no LLM)
            if poster_name:
                message = template.replace("{name}", poster_name)
            else:
                message = template.replace(" {name}", "").replace("{name}", "")
            message = message.replace("{LLM_TEXT}", "")
            message = re.sub(r'\n{3,}', '\n\n', message)

        print(f"  📝 Addressing message to: {poster_name or '(unknown name)'}")

        # Paste message via clipboard (Cmd+V)
        import subprocess
        from selenium.webdriver.common.keys import Keys
        subprocess.run(["pbcopy"], input=message.encode("utf-8"), check=True)
        text_area.click()
        random_sleep(0.3, 0.5)
        text_area.clear()
        random_sleep(0.3, 0.5)
        text_area.send_keys(Keys.COMMAND, "v")
        random_sleep(1, 2)

        # ── 6. Click send ────────────────────────────────────────────────────
        try:
            submit_btn = None
            for xp in [
                "//button[contains(.,'Abschicken')]",
                "//button[contains(.,'Nachricht senden')]",
                "//button[contains(.,'Anfrage senden')]",
                "//button[@type='submit' and contains(@class,'button-primary')]",
                "//button[@data-ng-click='submit()']",
            ]:
                try:
                    btns = driver.find_elements(By.XPATH, xp)
                    for btn in btns:
                        if btn.is_displayed():
                            submit_btn = btn
                            break
                    if submit_btn:
                        break
                except Exception:
                    continue

            if submit_btn:
                human_move_and_click(driver, submit_btn)
                random_sleep(2, 3)
                print("  ✅ Message sent successfully!")
                return True
            else:
                print("  ❌ Submit button not found!")
                return False
        except Exception as e:
            print(f"  ❌ Submit button error: {e}")
            return False

    except FileNotFoundError:
        print("  ❌ Message template file not found!")
        return False
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        print("".join(traceback.TracebackException.from_exception(e).format()))
        return False


def _fill_field(driver, selectors, value):
    """Try multiple CSS selectors to find and fill a form field using clipboard paste."""
    if not value:
        return
    import subprocess
    from selenium.webdriver.common.keys import Keys

    for sel in selectors:
        try:
            field = driver.find_element(By.CSS_SELECTOR, sel)
            if field.is_displayed():
                field.click()
                random_sleep(0.1, 0.2)
                # Select all + delete (clears any pre-filled value)
                field.send_keys(Keys.COMMAND, "a")
                field.send_keys(Keys.DELETE)
                random_sleep(0.1, 0.2)
                # Copy value to clipboard and paste
                subprocess.run(["pbcopy"], input=value.encode("utf-8"), check=True)
                field.send_keys(Keys.COMMAND, "v")
                random_sleep(0.2, 0.4)
                return
        except NoSuchElementException:
            continue
        except Exception:
            # Field found but not interactable (read-only, disabled, overlay, etc.)
            continue


def _fill_field_human(driver, selectors, value):
    """Fill a form field using human_type (keystroke-by-keystroke).
    Used during parallel LLM generation since clipboard can't be shared."""
    if not value:
        return
    from selenium.webdriver.common.keys import Keys

    for sel in selectors:
        try:
            field = driver.find_element(By.CSS_SELECTOR, sel)
            if field.is_displayed():
                field.click()
                random_sleep(0.1, 0.2)
                field.send_keys(Keys.COMMAND, "a")
                field.send_keys(Keys.DELETE)
                random_sleep(0.1, 0.2)
                human_type(field, value)
                random_sleep(0.2, 0.4)
                return
        except NoSuchElementException:
            continue
        except Exception:
            continue

