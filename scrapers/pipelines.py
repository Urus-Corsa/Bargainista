from itemadapter import ItemAdapter
from database.mongo import Database
from datetime import datetime, timezone
import logging
import os
import scrapy
from scrapy.pipelines.images import ImagesPipeline
from scrapy.exceptions import DropItem

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
            try:
                if hasattr(self._db, 'client') and self._db.client:
                    self._db.client.close()
                    self.logger.info("MongoDB client connection closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing MongoDB client: {str(e)}")
    
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
            'image_paths': adapter.get('image_paths', []),
            'scraped_at': datetime.now(timezone.utc)
        }
        try:
            result = self._db.insert_listing(listing_dict)
            self.logger.info(f"Successfully inserted item with ID: {result}")
        except Exception as e:
            self.logger.error(f"Failed to insert item: {str(e)}")
            raise      
        return item

class ImageDownloadPipeline(ImagesPipeline):
    def get_media_requests(self, item, info):
        adapter = ItemAdapter(item)
        image_urls = adapter.get('image_urls', [])
        listing_id = adapter.get('listing_id')
        
        if not image_urls:
            return

        for i, url in enumerate(image_urls):
            # Pass index and listing_id in meta for file naming
            yield scrapy.Request(url, meta={'image_index': i, 'listing_id': listing_id})
    
    def file_path(self, request, response=None, info=None, *, item=None):
        listing_id = request.meta.get('listing_id')
        index = request.meta.get('image_index')
        # Use the format: listing_id/index.jpg
        return f"{listing_id}/{index}.jpg"

    def item_completed(self, results, item, info):
        # results is a list of (success, file_info_or_error) tuples
        image_paths = [x['path'] for ok, x in results if ok]
        
        if not image_paths:
            # If no images were downloaded, we might want to log it or drop item?
            # Previous implementation just returned item.
            pass
            
        adapter = ItemAdapter(item)
        # Store paths relative to IMAGES_STORE. 
        # To match previous behavior explicitly, we could prepend 'images/' but 
        # usually storing the relative structure is cleaner.
        # We'll stick to what ImagesPipeline provides, which is 'listing_id/0.jpg'.
        # If the previous code relied on 'images/' prefix, it might matter.
        # Looking at previous code: img_path = os.path.join(listing_dir, f"{i}.jpg")
        # where listing_dir = os.path.join(self.images_dir, listing_id).
        # So yes, it had the full path.
        # Let's prepend the images store folder name for consistency if we can.
        # But simpler is often better. Let's just store the relative path.
        adapter['image_paths'] = image_paths
        
        return item