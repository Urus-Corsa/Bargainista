from itemadapter import ItemAdapter
from database.mongodb import Database
from datetime import datetime, timezone
import logging
import zoneinfo
import os
import requests
from PIL import Image
from io import BytesIO

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
            'scraped_at': datetime.now(timezone.utc)
        }
        try:
            result = self._db.insert_listing(listing_dict)
            self.logger.info(f"Successfully inserted item with ID: {result}")
        except Exception as e:
            self.logger.error(f"Failed to insert item: {str(e)}")
            raise      
        return item

class ImageDownloadPipeline:
    def __init__(self, images_dir):
        self.images_dir = images_dir
        self.logger = logging.getLogger(__name__)
        
    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            images_dir=crawler.settings.get('IMAGES_STORE', 'images')
        )
        
    def open_spider(self, spider):
        """Ensure the images directory exists when the spider starts"""
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
            self.logger.info(f"Created images directory: {self.images_dir}")
    
    def process_item(self, item, spider):
        """Download images for the listing and store their local paths"""
        adapter = ItemAdapter(item)
        listing_id = adapter.get('listing_id')
        image_urls = adapter.get('image_urls', [])
        
        if not listing_id:
            self.logger.warning("No listing_id found for item, skipping image download")
            return item
            
        if not image_urls:
            self.logger.debug(f"No image URLs found for listing {listing_id}")
            return item
        
        # Create listing-specific directory
        listing_dir = os.path.join(self.images_dir, listing_id)
        if not os.path.exists(listing_dir):
            os.makedirs(listing_dir)
            
        downloaded_images = []
        for i, url in enumerate(image_urls):
            try:
                response = requests.get(url, timeout=30)
                if response.status_code != 200:
                    self.logger.warning(f"Failed to download image {url}, status code: {response.status_code}")
                    continue
                    
                img = Image.open(BytesIO(response.content))
                img_path = os.path.join(listing_dir, f"{i}.jpg")
                img.save(img_path)
                downloaded_images.append(img_path)
                self.logger.debug(f"Downloaded image {i+1}/{len(image_urls)} for listing {listing_id}")
            except Exception as e:
                self.logger.error(f"Failed to download/process image {url}: {str(e)}")
                
        adapter['image_paths'] = downloaded_images
        self.logger.info(f"Downloaded {len(downloaded_images)}/{len(image_urls)} images for listing {listing_id}")
        return item