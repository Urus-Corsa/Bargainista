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