# -*- coding: utf-8 -*-
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

# ── LLM personalisation (optional) ─────────────────────────────────────────
# Set USE_LLM_PERSONALIZATION=true in .env to enable LLM-based personalisation.
# Backend is chosen via LLM_PROVIDER (gemma_local / gemini / openai) — see llm_personalizer.py.
_USE_LLM = os.getenv("USE_LLM_PERSONALIZATION", "false").lower() in ("1", "true", "yes")
if _USE_LLM:
    try:
        from llm_personalizer import personalise_message as _llm_personalise
        _llm_provider = os.getenv("LLM_PROVIDER", "gemma_local").strip().lower()
        _llm_model = {
            "gemma_local": "Gemma 3 4B-IT",
            "gemini": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "openai": os.getenv("OPENAI_MODEL", "gpt-5-nano"),
        }.get(_llm_provider, _llm_provider)
        print(f"🧠 LLM personalisation ENABLED ({_llm_model} via {_llm_provider})")
    except ImportError as _e:
        print(f"⚠️  LLM personalisation disabled — import error: {_e}")
        _USE_LLM = False

WG_EMAIL    = os.getenv("WG_EMAIL")
WG_PASSWORD = os.getenv("WG_PASSWORD")

if not WG_EMAIL or not WG_PASSWORD:
    raise ValueError(
        "WG_EMAIL and WG_PASSWORD must be set in your .env file. "
        "Copy .env.example to .env and fill in your credentials."
    )

CAPTCHA_WAIT_SECONDS = 120  # How long to wait for manual CAPTCHA solving

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def random_sleep(min_s=1.0, max_s=3.0):
    """Sleep for a random duration to mimic human behaviour."""
    time.sleep(random.uniform(min_s, max_s))


def human_type(element, text, target_seconds=None):
    """
    Type text character-by-character with randomised delays,
    just like a real human would.

    If `target_seconds` is given, the per-character delay is paced so the
    whole string takes roughly that long to type (with jitter so it doesn't
    look perfectly uniform) instead of the default 0.04-0.18s/char, which
    would take minutes on long messages.
    """
    if not text:
        return
    if target_seconds is not None:
        per_char = target_seconds / len(text)
        for char in text:
            element.send_keys(char)
            time.sleep(max(0.005, random.uniform(0.7, 1.3) * per_char))
    else:
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.04, 0.18))


def human_move_and_click(driver, element):
    """
    Move the mouse gradually toward an element before clicking,
    to avoid the instant-teleport pattern that bot detectors flag.
    """
    actions = ActionChains(driver)
    actions.move_to_element(element)
    actions.pause(random.uniform(0.2, 0.6))
    actions.click(element)
    actions.perform()


def wait_for(driver, by, value, timeout=15):
    """Wait until an element is present and visible; return it or None."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((by, value))
        )
        return el
    except TimeoutException:
        return None


def solve_captcha_if_present(driver):
    """
    Detect a *visible* reCAPTCHA / hCaptcha and pause for manual solving.
    wg-gesucht embeds invisible CAPTCHA iframes for telemetry — we only
    pause if the iframe is actually displayed and has a non-zero size.
    """
    def is_visible(iframe):
        try:
            return iframe.is_displayed() and iframe.size.get("height", 0) > 10
        except Exception:
            return False

    recaptcha = [f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']") if is_visible(f)]
    hcaptcha  = [f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha']")  if is_visible(f)]

    if not (recaptcha or hcaptcha):
        return  # Not visible — carry on

    print(f"  ⚠️  Visible CAPTCHA detected! Please solve it in the browser within {CAPTCHA_WAIT_SECONDS}s…")
    deadline = time.time() + CAPTCHA_WAIT_SECONDS
    while time.time() < deadline:
        still_visible = [
            f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha'], iframe[src*='hcaptcha']")
            if is_visible(f)
        ]
        if not still_visible:
            print("  ✅ CAPTCHA solved. Continuing…")
            return
        time.sleep(2)
    print("  ⚠️  CAPTCHA wait timed out — continuing anyway.")


def create_driver():
    """
    Create a stealth Chrome instance using selenium + webdriver-manager.
    Uses a unique temporary profile per session to avoid conflicts with
    any already-open Chrome windows.
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

    # Use a persistent profile so login session/cookies are saved between runs.
    # Stored separately from the user's regular Chrome (no conflict).
    profile_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "data", "wgbot_profile"
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
# Name extraction
# ---------------------------------------------------------------------------

def extract_poster_name(driver, listing_url):
    """
    Extract the poster's first name.

    Strategy:
      1. PRIMARY — /nachricht-senden/ page: WG-Gesucht always renders the
         recipient name as plain text there (e.g. "Nachricht an Tom"),
         even when the listing page shows it as a PNG image.
      2. FALLBACK — regex scan through the ad description text.
    """
    slug = listing_url.lstrip("/")
    nachricht_url = f"https://www.wg-gesucht.de/nachricht-senden/{slug}"


    # Strategy 1: Nachricht-senden page — name in <b> inside Mitglied seit card
    # Exact HTML structure (from debug):
    #   <div class="ml10"><b>Monika</b><br>Mitglied seit: ...</div>
    try:
        driver.get(nachricht_url)
        random_sleep(2, 4)
        solve_captcha_if_present(driver)

        found_name = ""
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
                    # Check for initials like "V. Vivanco"
                    elif len(words) > 1 and words[0].endswith("."):
                        text = f"{words[0]} {words[1]}"
                    else:
                        text = words[0]
                        
                    if len(text) >= 3 and text[0].isupper() and text.replace(" ", "").replace("-", "").replace(".", "").isalpha():
                        found_name = text
                        break
                if found_name:
                    break
            except Exception:
                continue

        if found_name:
            print(f"  \u2713 Poster name (Mitglied card): {found_name}")
            return found_name

    except Exception as e:
        print(f"  \u26a0 Nachricht page error: {e}")

    print("  \u26a0 Could not extract poster name \u2014 using generic greeting.")
    return ""


# ---------------------------------------------------------------------------
# Main submit function
# ---------------------------------------------------------------------------

def init_driver_and_login():
    """
    Launch Chrome, open WG-Gesucht, handle cookies and log in.
    Returns the ready-to-use driver instance.
    """
    driver = create_driver()

    try:
        # ── 1. Open homepage and handle cookie banner ────────────────────────
        print(f"\n🌐 Opening WG-Gesucht homepage to establish session…")
        driver.get("https://www.wg-gesucht.de/")
        random_sleep(2, 4)

        # Dismiss Chrome's "Restore pages?" bubble if present (appears after crash)
        try:
            restore_close = driver.find_elements(
                By.XPATH, "//button[contains(.,'Don') or contains(.,'Nicht')]"
            )
            if restore_close:
                human_move_and_click(driver, restore_close[0])
                random_sleep(0.5, 1)
        except Exception:
            pass

        # Cookie banner — handles both German ('Akzeptieren') and English ('Accept all')
        try:
            banners = driver.find_elements(
                By.XPATH,
                "//*[contains(text(),'Akzeptieren') or contains(text(),'Accept all') or contains(text(),'Alle akzeptieren')]"
            )
            if banners:
                human_move_and_click(driver, banners[0])
                random_sleep(1, 2)
                print("  🍪 Cookie/consent banner accepted.")
        except Exception:
            pass

        solve_captcha_if_present(driver)

        # ── 2. Log in ────────────────────────────────────────────────────────
        try:
            konto_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'Mein Konto')]")
            if konto_btns:
                human_move_and_click(driver, konto_btns[0])
                random_sleep(1, 2)

                email_field = wait_for(driver, By.ID, "login_email_username", timeout=8)
                if email_field:
                    human_type(email_field, WG_EMAIL)
                    random_sleep(0.5, 1.2)
                    pwd_field = driver.find_element(By.ID, "login_password")
                    human_type(pwd_field, WG_PASSWORD)
                    random_sleep(0.5, 1.5)
                    login_btn = driver.find_element(By.ID, "login_submit")
                    human_move_and_click(driver, login_btn)
                    random_sleep(3, 5)   # wait for redirect after login
                    solve_captcha_if_present(driver)
                    print("  🔑 Logged in successfully.")
                else:
                    print("  ⚠️ Login form not found after clicking 'Mein Konto'.")
            else:
                # No login button visible — already logged in
                print("  ℹ️  Already logged in (no 'Mein Konto' button found).")
        except Exception as e:
            print(f"  ⚠️ Login step error: {e}")

        # Verify logged in — check for nav items that only appear when authenticated
        page_src = driver.page_source.lower()
        logged_in = any(kw in page_src for kw in ["abmelden", "meine-anzeigen", "logout", "mein konto\nübersicht"])
        if not logged_in:
            print(f"  ⚠️  Login may have failed. Current URL: {driver.current_url}")
            print("     Proceeding anyway (session cookie may still work).")

    except Exception as e:
        print(f"  ❌ Critical login error: {e}")

    return driver


def submit_app(driver, ref):
    """
    Given an already logged-in driver, extract poster name, and send a personalised message.
    """
    try:


        # ── 3. Extract poster name from listing page ─────────────────────────
        print(f"\n📋 Extracting poster name for: {ref}")
        poster_name = extract_poster_name(driver, ref)
        
        # Skip known commercial agencies / booking platforms
        if poster_name.lower() in ["housinganywhere", "uniplaces", "spotahome", "medici", "spacest"]:
            print(f"  ⏭  Skipping commercial agency/partner ad: {poster_name}")
            return

        # ── 4. Navigate to the message-sending page ──────────────────────────
        # URL format: /nachricht-senden/<full-listing-slug>
        # e.g. ref = /wg-zimmer-in-Muenchen-Schwabing.12345.html
        #   -> /nachricht-senden/wg-zimmer-in-Muenchen-Schwabing.12345.html
        slug = ref.lstrip("/")  # remove leading slash from the listing ref
        msg_url = f"https://www.wg-gesucht.de/nachricht-senden/{slug}"
        print(f"  🌐 Navigating to message page: {msg_url}")
        driver.get(msg_url)
        random_sleep(3, 5)
        solve_captcha_if_present(driver)

        # ── 5. Security/safety tips modal (Wichtige Sicherheitstipps) ────────
        # Try by ID first, then by button text (wg-gesucht shows this on first contact)
        try:
            se_btn = driver.find_element(By.ID, "sicherheit_bestaetigung")
            human_move_and_click(driver, se_btn)
            random_sleep(1, 2)
            print("  🔒 Security confirmation clicked (by ID).")
        except NoSuchElementException:
            try:
                se_btn = driver.find_element(
                    By.XPATH,
                    "//button[contains(.,'Sicherheitstipps gelesen') or contains(.,'Ich habe die Sicherheitstipps')]"
                )
                human_move_and_click(driver, se_btn)
                random_sleep(1, 2)
                print("  🔒 Security tips modal dismissed.")
            except NoSuchElementException:
                pass

        # ── 6. Skip if already messaged ──────────────────────────────────────
        try:
            driver.find_element(By.ID, "message_timestamp")
            print("  ⏭  Message already sent to this listing. Skipping.")
            return
        except NoSuchElementException:
            print("  ✉  Composing message…")

        # ── 7. Find message input and type message ───────────────────────────
        text_area = wait_for(driver, By.ID, "message_input", timeout=10)
        if not text_area:
            # Debug: print current URL and page title
            print(f"  ❌ Message input not found! Current URL: {driver.current_url}")
            print(f"     Page title: {driver.title}")
            return

        text_area.clear()
        message_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config", "message.txt"
        )
        with open(message_path, "r", encoding="utf-8") as f:
            template = f.read()

        # ── Optionally personalise with the configured LLM ─────────────────
        if _USE_LLM:
            print(f"  🧠 Personalising message with {_llm_model}…")
            
            # We are currently on the 'nachricht-senden' page.
            # To get the description, we must briefly load the actual listing page.
            current_msg_url = driver.current_url
            driver.get("https://www.wg-gesucht.de/" + slug)
            random_sleep(1, 2)
            solve_captcha_if_present(driver)

            # WG-Gesucht sometimes shows a standalone "Überprüfung" reCAPTCHA
            # page instead of the listing (seen when navigating too fast).
            # solve_captcha_if_present() only handles the iframe widget itself,
            # so also detect the wrapper page and wait for it to clear before
            # giving up on the description (a silently empty description means
            # no LLM personalisation and just the bare template gets sent).
            if "warum erscheint diese seite" in driver.page_source.lower():
                print("  ⚠️  Verification/CAPTCHA page shown instead of listing — waiting for it to clear…")
                deadline = time.time() + CAPTCHA_WAIT_SECONDS
                while time.time() < deadline and "warum erscheint diese seite" in driver.page_source.lower():
                    time.sleep(2)
                if "warum erscheint diese seite" in driver.page_source.lower():
                    print("  ⚠️  Still on verification page — retrying listing page load once…")
                    driver.get("https://www.wg-gesucht.de/" + slug)
                    random_sleep(1, 2)
                    solve_captcha_if_present(driver)

            description = ""
            full_desc = []
            import re
            for _id, title in [("freitext_0", "Zimmer:"), ("freitext_1", "Lage:"), ("freitext_2", "WG-Leben:"), ("freitext_3", "Sonstiges:")]:
                try:
                    el = driver.find_element(By.ID, _id)
                    raw_text = el.get_attribute("textContent") or ""
                    clean_text = re.sub(r"googletag\.cmd\.push\([\s\S]*?\}\);", "", raw_text).strip()
                    if clean_text:
                        full_desc.append(f"--- {title} ---\n{clean_text}")
                except NoSuchElementException:
                    pass
                    
            if full_desc:
                description = "\n\n".join(full_desc)
            else:
                try:
                    desc_el = driver.find_element(By.ID, "ad_description_text")
                    description = desc_el.text.strip()
                except NoSuchElementException:
                    pass

            if not description.strip():
                print("  ⚠️  Could not extract listing description — message will fall back to the plain template (no LLM paragraph).")

            # Return to the message page
            driver.get(current_msg_url)
            random_sleep(1, 2)
            text_area = wait_for(driver, By.ID, "message_input", timeout=10)
            message = _llm_personalise(
                template=template,
                listing_description=description,
                poster_name=poster_name,
            )
        else:
            # Plain substitution
            if poster_name:
                message = template.replace("{name}", poster_name)
            else:
                message = template.replace(" {name}", "").replace("{name}", "")

        print(f"  📝 Addressing message to: {poster_name or '(unknown name)'}")
        # Type it out like a human would, but paced to take ~25s regardless of
        # message length (a fixed per-char delay would take 2+ minutes on long
        # messages, which is unnecessarily slow).
        human_type(text_area, message, target_seconds=25)
        random_sleep(1, 2)

        # ── 8. Click send ────────────────────────────────────────────────────
        # Button text is 'Senden' on wg-gesucht (not 'Nachricht senden')
        try:
            submit_btn = driver.find_element(
                By.XPATH,
                "//button[@data-ng-click='submit()' or contains(.,'Senden') or contains(.,'Nachricht senden')]"
            )
            human_move_and_click(driver, submit_btn)
            random_sleep(2, 3)
            print("  ✅ Message sent successfully!")
        except NoSuchElementException:
            print("  ❌ Submit button not found!")

    except FileNotFoundError:
        print("  ❌ message.txt not found!")
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        print("".join(traceback.TracebackException.from_exception(e).format()))
