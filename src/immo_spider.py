import os
import scrapy
from dotenv import load_dotenv

load_dotenv()


class ImmoScoutSpider(scrapy.Spider):
    name = "immoscout"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urls_env = os.getenv("IMMO_SEARCH_URLS", "")
        if urls_env:
            self.start_urls = [u.strip() for u in urls_env.split(",") if u.strip()]
        else:
            self.start_urls = []
            self.logger.warning(
                "IMMO_SEARCH_URLS is not set in .env! The spider has no URLs to crawl."
            )

    def parse(self, response):
        for quote in response.css(
            'a.result-list-entry__brand-title-container::attr(href)'
        ).extract():
            yield {"href": quote}
