import os
import sys
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from scrapers.spiders.craigslist import CraigslistSpider
import logging

os.environ['SCRAPY_SETTINGS_MODULE'] = 'scrapers.settings'

def run_spider():
    logger = logging.getLogger(__name__)
    settings = get_project_settings()
    process = CrawlerProcess(settings)
    locations = sys.argv[1] if len(sys.argv) > 1 else None
    logger.info(f"Starting spider with locations:{locations}")
    process.crawl(CraigslistSpider, locations=locations)
    process.start()

if __name__ == '__main__':
    run_spider()