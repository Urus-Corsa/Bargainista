from itemadapter import ItemAdapter
from database.mongodb import Database
from datetime import datetime, timezone
import logging
import zoneinfo

class MongoDBPipeline:
    def __init__(self, mongo_uri):
        self.mongo_uri = mongo_uri
        self._db = None
        self.logger = logging.getLogger(__name__)
    
    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            mongo_uri=crawler.settings.get('MONGODB_URI')
        )
    
    def open_spider(self, spider):
        self._db = Database(self.mongo_uri)
    
    def close_spider(self, spider):
        if self._db:
            self._db.client.close()
    
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)

        listing_dict = {
            'source': adapter.get('source'),
            'listing_id': adapter.get('listing_id'),
            'url': adapter.get('url'),
            'title': adapter.get('title'),
            'price': adapter.get('price'),
            'location': adapter.get('location'),
            'posting_date': adapter.get('posting_date'),
            'description': adapter.get('description'),
            'make': adapter.get('make'),
            'model': adapter.get('model'),
            'year': adapter.get('year'),
            'mileage': adapter.get('mileage'),
            'image_urls': adapter.get('image_urls', []),
            'scraped_at': datetime.now(timezone.utc)
        }
        try:
            result = self._db.insert_listing(listing_dict)
            self.logger.info(f"Successfully inserted item with ID: {result}")
        except Exception as e:
            self.logger.error(f"Failed to insert item: {str(e)}")
            raise      
        return item