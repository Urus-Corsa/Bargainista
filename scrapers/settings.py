from dotenv import load_dotenv
import os

# Load .env file
load_dotenv()

BOT_NAME = 'bargainista'
SPIDER_MODULES = ['scrapers.spiders']
NEWSPIDER_MODULE = 'scrapers.spiders'
ROBOTSTXT_OBEY = False
CONCURRENT_REQUESTS = 5
DOWNLOAD_DELAY = 3
COOKIES_ENABLED = False
ITEM_PIPELINES = {
   'scrapers.pipelines.MongoDBPipeline': 300,
}
MONGODB_URI = os.getenv('MONGODB_CONNECTION')
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 5
AUTOTHROTTLE_MAX_DELAY = 60
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
LOG_LEVEL = 'DEBUG'
LOG_FILE = 'craigsspider_logs.txt'