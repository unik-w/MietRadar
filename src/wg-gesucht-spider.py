import os
import scrapy
from dotenv import load_dotenv

load_dotenv()


class WgGesuchtSpider(scrapy.Spider):
    name = "wg-gesucht"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urls_env = os.getenv("WG_SEARCH_URLS", "")
        if urls_env:
            self.start_urls = [u.strip() for u in urls_env.split(",") if u.strip()]
        else:
            self.start_urls = []
            self.logger.warning(
                "WG_SEARCH_URLS is not set in .env! The spider has no URLs to crawl."
            )

    def parse(self, response):
        # Parse each individual listing card on the search results page
        for card in response.css('.wgg_card'):
            href = card.css('h2.truncate_title a::attr(href)').get()
            if not href:
                continue
                
            # Internal WG-Gesucht listings use relative URLs (starting with /).
            # External or sponsored ads generally use absolute URLs or contain tracking tokens.
            if not href.startswith('/') or 'asset_id' in href:
                continue
                
            # Extract all raw text content from the card to check for commercial agencies
            card_text = "".join(card.css('::text').getall()).lower()
            
            # Skip any card that mentions known corporate booking platforms in the preview
            agencies = ["housinganywhere", "spacest", "medici", "spotahome", "uniplaces"]
            if any(agency in card_text for agency in agencies):
                self.logger.info(f"Skipping agency listing directly from search: {href}")
                continue

            yield {"data-id": href}

