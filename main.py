import os
import sys
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from scrapers.spiders.craigslist import CraigslistSpider
from utils.logging import configure_scrapy_logging

os.environ['SCRAPY_SETTINGS_MODULE'] = 'scrapers.settings'

def run_spider():
    settings = get_project_settings()
    settings = configure_scrapy_logging(settings)
    process = CrawlerProcess(settings)
    locations = sys.argv[1] if len(sys.argv) > 1 else None
    process.crawl(CraigslistSpider, locations=locations)
    process.start()

if __name__ == '__main__':
    run_spider()