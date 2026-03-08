# -*- coding: utf-8 -*-
import os
import time
import traceback

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# Load credentials from .env file
load_dotenv()

IMMO_SALUTATION = os.getenv("IMMO_SALUTATION", "Herr")
IMMO_LAST_NAME = os.getenv("IMMO_LAST_NAME")
IMMO_FIRST_NAME = os.getenv("IMMO_FIRST_NAME")
IMMO_EMAIL = os.getenv("IMMO_EMAIL")
IMMO_STREET = os.getenv("IMMO_STREET")
IMMO_HOUSE_NUMBER = os.getenv("IMMO_HOUSE_NUMBER")
IMMO_POSTCODE = os.getenv("IMMO_POSTCODE")
IMMO_CITY = os.getenv("IMMO_CITY")

# Validate required fields
_required = {
    "IMMO_LAST_NAME": IMMO_LAST_NAME,
    "IMMO_FIRST_NAME": IMMO_FIRST_NAME,
    "IMMO_EMAIL": IMMO_EMAIL,
    "IMMO_STREET": IMMO_STREET,
    "IMMO_HOUSE_NUMBER": IMMO_HOUSE_NUMBER,
    "IMMO_POSTCODE": IMMO_POSTCODE,
    "IMMO_CITY": IMMO_CITY,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise ValueError(
        f"The following env vars must be set in your .env file: {', '.join(_missing)}. "
        "Copy .env.example to .env and fill in your details."
    )


def submit_app(ref):
    # Automatically download and manage the correct chromedriver
    chrome_options = webdriver.ChromeOptions()
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get('https://www.immobilienscout24.de' + ref + '#/basicContact/email')
    driver.implicitly_wait(10)

    try:
        el = driver.find_element('id', 'contactForm-salutation')
        for option in el.find_elements('tag name', 'option'):
            if option.text == IMMO_SALUTATION:
                option.click()
                break
        last_name = driver.find_element('id', 'contactForm-lastName')
        last_name.send_keys(IMMO_LAST_NAME)
        first_name = driver.find_element('id', 'contactForm-firstName')
        first_name.send_keys(IMMO_FIRST_NAME)
        email = driver.find_element('id', 'contactForm-emailAddress')
        email.send_keys(IMMO_EMAIL)
        street = driver.find_element('id', 'contactForm-street')
        street.send_keys(IMMO_STREET)
        house = driver.find_element('id', 'contactForm-houseNumber')
        house.send_keys(IMMO_HOUSE_NUMBER)
        post = driver.find_element('id', 'contactForm-postcode')
        post.send_keys(IMMO_POSTCODE)
        city = driver.find_element('id', 'contactForm-city')
        city.send_keys(IMMO_CITY)
        text_area = driver.find_element('id', 'contactForm-Message')
        text_area.clear()

        # Read message from file
        message_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'message.txt')
        with open(message_path, 'r', encoding='utf-8') as f:
            message = f.read()
        text_area.send_keys(message)

        submit_button1 = driver.find_element(
            'xpath',
            "//button[@data-ng-click='submit()' or contains(.,'Anfrage senden')]"
        )
        time.sleep(5)
        submit_button1.click()
        time.sleep(3)

        submit_button = driver.find_element(
            'xpath',
            "//button[@data-ng-click='submit()' or contains(.,'Anfrage senden')]"
        )
    except NoSuchElementException as e:
        print("Unable to find HTML element")
        print("".join(traceback.TracebackException.from_exception(e).format()))
    finally:
        driver.quit()
