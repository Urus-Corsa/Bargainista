from datetime import datetime
from typing import List, Dict, Any
from pymongo import MongoClient, uri_parser
from bson import ObjectId
import logging
import certifi
import time

class Database:
    def __init__(self, connection_string: str):
        self.logger = logging.getLogger(__name__)
        self.connection_string = connection_string
        self.client = None
        self.db = None
        self._connect_with_retry()

    def _connect_with_retry(self, max_retries=3, retry_delay=2):
        retries = 0
        last_exception = None

        while retries < max_retries:
            try:
                self.logger.info(f"Attempting to connect to MongoDB (attempt {retries+1}/{max_retries})")
                
                # Parse connection string to extract database name
                parsed_uri = uri_parser.parse_uri(self.connection_string)
                db_name = parsed_uri.get('database') or 'bargainista'
                
                # Create MongoDB client with generous timeouts
                self.client = MongoClient(
                    self.connection_string,
                    serverSelectionTimeoutMS=10000,
                    connectTimeoutMS=10000,
                    socketTimeoutMS=20000,
                    ssl=True,
                    tlsCAFile=certifi.where()
                )
                
                self.client.admin.command('ping')
                
                self.db = self.client[db_name]
                self.logger.info(f"Successfully connected to MongoDB database: {db_name}")
                return
            except Exception as e:
                last_exception = e
                self.logger.warning(f"Connection attempt {retries+1} failed: {str(e)}")
                retries += 1
                if retries < max_retries:
                    time.sleep(retry_delay)
        
        self.logger.error(f"Failed to connect to MongoDB after {max_retries} attempts. Last error: {str(last_exception)}")
        

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

    def find_comparable_listings(self, make: str, model: str, year: int = None, 
                                mileage_range: tuple = None, days_back: int = 90,
                                limit: int = 50) -> List[Dict[str, Any]]:
        """Find comparable listings based on make, model, year and mileage range"""
        try:
            query = {'make': make, 'model': model}
            
            if year:
                # Look for cars within 1 year range
                query['year'] = {'$gte': year - 1, '$lte': year + 1}
                
            if mileage_range and len(mileage_range) == 2:
                query['mileage'] = {'$gte': mileage_range[0], '$lte': mileage_range[1]}
                
            # Only consider recent listings
            if days_back:
                cutoff_date = datetime.now() - timedelta(days=days_back)
                query['scraped_at'] = {'$gte': cutoff_date}
                
            self.logger.debug(f"Finding comparable listings with query: {query}")
            results = list(self.db.listings.find(query).limit(limit))
            self.logger.info(f"Found {len(results)} comparable listings")
            return results
        except Exception as e:
            self.logger.error(f"Error finding comparable listings: {str(e)}")
            return []
            
    def get_listing(self, listing_id: str) -> Dict[str, Any]:
        """Get a single listing by its ID"""
        try:
            result = self.db.listings.find_one({'listing_id': listing_id})
            return result
        except Exception as e:
            self.logger.error(f"Error getting listing {listing_id}: {str(e)}")
            return None
            
    def update_listing(self, listing_id: str, update_data: Dict[str, Any]) -> bool:
        """Update a listing with new data"""
        try:
            result = self.db.listings.update_one(
                {'listing_id': listing_id},
                {'$set': update_data}
            )
            success = result.modified_count > 0
            self.logger.debug(f"Updated listing {listing_id}: {success}")
            return success
        except Exception as e:
            self.logger.error(f"Error updating listing {listing_id}: {str(e)}")
            return False
            
    def find_listings_with_high_bargain_score(self, min_score: float = 80, limit: int = 20) -> List[Dict[str, Any]]:
        """Find listings with high bargain scores"""
        try:
            query = {'bargain_analysis.score': {'$gte': min_score}}
            results = list(self.db.listings.find(query)
                          .sort('bargain_analysis.score', -1)
                          .limit(limit))
            self.logger.info(f"Found {len(results)} listings with bargain score >= {min_score}")
            return results
        except Exception as e:
            self.logger.error(f"Error finding listings with high bargain scores: {str(e)}")
            return []
            
    def get_recent_listings(self, days_back: int = 30, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent listings that need to be processed"""
        try:
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            # Find listings newer than cutoff date
            query = {'scraped_at': {'$gte': cutoff_date}}
            
            # Check if they've been processed already (using a new field we'll add)
            query['processed'] = {'$ne': True}
            
            # Sort by most recent first
            results = list(self.db.listings.find(query)
                          .sort('scraped_at', -1)
                          .limit(limit))
            
            self.logger.info(f"Found {len(results)} recent unprocessed listings")
            return results
        except Exception as e:
            self.logger.error(f"Error getting recent listings: {str(e)}")
            return []
    
    def get_historical_listings(self, days_back: int = 365, limit: int = 100) -> List[Dict[str, Any]]:
        """Get historical listings regardless of image availability"""
        try:
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            # Find listings older than the regular processing window but within historical window
            query = {
                'scraped_at': {'$gte': cutoff_date},
                'processed_historical': {'$ne': True}
            }
            
            # Sort by oldest first to process chronologically
            results = list(self.db.listings.find(query)
                          .sort('scraped_at', 1)
                          .limit(limit))
            
            self.logger.info(f"Found {len(results)} historical listings to process")
            return results
        except Exception as e:
            self.logger.error(f"Error getting historical listings: {str(e)}")
            return []
    
    def mark_listing_as_processed(self, listing_id: str, historical: bool = False) -> bool:
        """Mark a listing as processed so it's not re-processed"""
        try:
            update_fields = {
                'processed': True,
                'processed_at': datetime.now()
            }
            
            # If this is historical processing, mark that separately
            if historical:
                update_fields['processed_historical'] = True
            
            result = self.db.listings.update_one(
                {'listing_id': listing_id},
                {'$set': update_fields}
            )
            success = result.modified_count > 0
            if success:
                self.logger.debug(f"Marked listing {listing_id} as processed {'(historical)' if historical else ''}")
            else:
                self.logger.warning(f"Failed to mark listing {listing_id} as processed")
            return success
        except Exception as e:
            self.logger.error(f"Error marking listing {listing_id} as processed: {str(e)}")
            return False