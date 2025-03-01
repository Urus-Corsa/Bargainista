from datetime import datetime, timedelta
from typing import List, Dict, Any
from pymongo import MongoClient
from bson import ObjectId
import logging
# from models.listings import CarListing  #pydantic
class Database:
    def __init__(self, connection_string: str):
        self.logger = logging.getLogger(__name__)
        self.client = MongoClient(connection_string)
        self.db = self.client.bargainista
        self.logger.info("Database connection initialized")

    def listing_exists(self, listing_id: str) -> bool:
        try:
            exists = self.db.listings.find_one({'listing_id': listing_id}) is not None
            self.logger.debug(f"Checking if listing {listing_id} exists: {exists}")
            return exists
        except Exception as e:
            self.logger.error(f"Error checking listing existence: {str(e)}")
            return False

    def insert_listing(self, listing: Dict[str, Any]) -> ObjectId:
        try:
            listing_id = listing.get('listing_id')
            if listing_id and self.listing_exists(listing_id):
                self.logger.info(f"Listing {listing_id} already exists, skipping insertion")
                return None
            
            self.logger.debug(f"Attempting to insert listing: {listing.get('title', 'No title')}")
            result = self.db.listings.insert_one(listing)
            self.logger.info(f"Successfully inserted listing with ID: {result.inserted_id}")
            return result.inserted_id
        except Exception as e:
            self.logger.error(f"Failed to insert listing: {str(e)}")
            raise

    def _build_similar_query(self, make: str, model: str, year: int, max_age_days: int) -> dict:
        return {
            'make': make,
            'model': model,
            'year': {'$gte': year - 1, '$lte': year + 1},
            'scraped_at': {'$gte': datetime.now() - timedelta(days=max_age_days)}
        }

    def get_similar_listings(self, make: str, model: str, year: int, max_age_days: int = 30) -> List[Dict[str, Any]]: #List[CarListing] pydantic
        query = self._build_similar_query(make, model, year, max_age_days)
        cursor = self.db.listings.find(query)
        return list(cursor)
        # return [CarListing(**doc) for doc in await cursor.to_list(length=100)] #pydantic