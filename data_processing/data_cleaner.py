import logging
import os
import re
from typing import Dict, Any, List, Tuple, Optional
from database.mongo import Database
from database.postgres import PostgresDB

class DataCleaner:
    """
    Responsible for cleaning and normalizing raw car listing data
    from MongoDB and storing it in PostgreSQL.
    """
    
    def __init__(self, mongo_db: Database, postgres_db: PostgresDB):
        """Initialize with database connections."""
        self.mongo_db = mongo_db
        self.postgres_db = postgres_db
        self.logger = logging.getLogger(__name__)
        
        # Load manufacturer/model normalization maps
        self.make_corrections = {
            'chevy': 'chevrolet',
            'chev': 'chevrolet',
            'vw': 'volkswagen',
            'mercedes': 'mercedes-benz',
            'merc': 'mercedes-benz',
            'subaru wrx': 'subaru',  # In case make contains model
            'bmw m': 'bmw',  # In case make contains model series
            'ford mustang': 'ford',  # In case make contains model
            'toyota camry': 'toyota',  # In case make contains model
        }
        
    def _normalize_make(self, make: str) -> str:
        """Normalize manufacturer name."""
        if not make:
            return None
            
        make_lower = make.lower().strip()
        
        # Apply known corrections
        for incorrect, correct in self.make_corrections.items():
            if make_lower == incorrect or make_lower.startswith(incorrect + ' '):
                return correct
                
        return make_lower
    
    def _normalize_model(self, make: str, model: str) -> str:
        """Normalize model name based on manufacturer."""
        if not model:
            return None
            
        model_lower = model.lower().strip()
        
        # Handle case where make is in the model field
        if make and make.lower() in model_lower:
            model_lower = model_lower.replace(make.lower(), '').strip()
            
        # Remove common prefixes that might be in model name
        prefixes_to_remove = [f"{make.lower()} ", "new ", "used "]
        for prefix in prefixes_to_remove:
            if model_lower.startswith(prefix):
                model_lower = model_lower[len(prefix):].strip()
        
        return model_lower
    
    def _clean_price(self, price: Any) -> Optional[int]:
        """Clean and validate price data."""
        if price is None:
            return None
            
        # If already an integer, validate reasonable range
        if isinstance(price, int):
            if 100 <= price <= 1000000:  # Reasonable price range for cars
                return price
            else:
                self.logger.warning(f"Price out of reasonable range: {price}, setting to None")
                return None
        
        # Try to extract numeric value from string
        if isinstance(price, str):
            price_str = price.replace('$', '').replace(',', '').strip()
            try:
                price_int = int(price_str)
                if 100 <= price_int <= 1000000:
                    return price_int
                else:
                    self.logger.warning(f"Extracted price out of reasonable range: {price_int}, setting to None")
                    return None
            except ValueError:
                self.logger.warning(f"Could not parse price from: {price}")
                return None
        
        self.logger.warning(f"Unsupported price format: {price}, type: {type(price)}")
        return None
    
    def _clean_year(self, year: Any) -> Optional[int]:
        """Clean and validate year data."""
        if year is None:
            return None
            
        # If already an integer, validate reasonable range
        if isinstance(year, int):
            if 1900 <= year <= 2030:  # Reasonable year range
                return year
            else:
                self.logger.warning(f"Year out of reasonable range: {year}, setting to None")
                return None
        
        # Try to extract year from string
        if isinstance(year, str):
            year_match = re.search(r'(19\d{2}|20\d{2})', year)
            if year_match:
                year_int = int(year_match.group(1))
                if 1900 <= year_int <= 2030:
                    return year_int
                else:
                    self.logger.warning(f"Extracted year out of reasonable range: {year_int}, setting to None")
                    return None
            else:
                self.logger.warning(f"Could not extract year from: {year}")
                return None
        
        self.logger.warning(f"Unsupported year format: {year}, type: {type(year)}")
        return None
    
    def _clean_mileage(self, mileage: Any) -> Optional[int]:
        """Clean and validate mileage data."""
        if mileage is None:
            return None
            
        # If already an integer, validate reasonable range
        if isinstance(mileage, int):
            if 0 <= mileage <= 1000000:  # Reasonable mileage range
                return mileage
            else:
                self.logger.warning(f"Mileage out of reasonable range: {mileage}, setting to None")
                return None
        
        # Try to extract numeric value from string
        if isinstance(mileage, str):
            # Remove 'mi', 'miles', 'k', commas, etc.
            mileage_str = re.sub(r'[^\d.]', '', mileage)
            try:
                # Check if 'k' or similar suffix was used (e.g., 50k miles)
                multiplier = 1000 if 'k' in mileage.lower() else 1
                mileage_int = int(float(mileage_str) * multiplier)
                if 0 <= mileage_int <= 1000000:
                    return mileage_int
                else:
                    self.logger.warning(f"Extracted mileage out of reasonable range: {mileage_int}, setting to None")
                    return None
            except ValueError:
                self.logger.warning(f"Could not parse mileage from: {mileage}")
                return None
        
        self.logger.warning(f"Unsupported mileage format: {mileage}, type: {type(mileage)}")
        return None
    
    def _normalize_location(self, location: Any) -> Optional[str]:
        """Normalize location data."""
        if not location:
            return None
            
        # Handle case where location is a list
        if isinstance(location, list):
            if len(location) > 0:
                # Join multiple location parts with commas
                return ', '.join(str(loc).strip() for loc in location if loc)
            return None
        
        # Handle string case
        if isinstance(location, str):
            return location.strip()
        
        # Convert other types to string
        return str(location).strip()
    
    def _prepare_image_data(self, mongo_listing: Dict[str, Any]) -> List[Dict[str, str]]:
        """Prepare image data for PostgreSQL insertion."""
        image_data = []
        
        # Get image URLs and local paths
        image_urls = mongo_listing.get('image_urls', [])
        image_paths = mongo_listing.get('image_paths', [])
        
        # If we have both URLs and paths
        if image_urls and image_paths:
            for i, (url, path) in enumerate(zip(image_urls, image_paths)):
                if url and path:
                    image_data.append({
                        'url': url,
                        'path': path
                    })
        # If we only have URLs
        elif image_urls:
            for url in image_urls:
                if url:
                    image_data.append({
                        'url': url,
                        'path': None  # No local path available
                    })
        
        return image_data
    
    def clean_listing(self, raw_listing: Dict[str, Any]) -> Dict[str, Any]:
        """Clean and normalize a single listing."""
        if not raw_listing:
            return None
            
        # Extract and normalize make/model
        make = self._normalize_make(raw_listing.get('make'))
        model = self._normalize_model(raw_listing.get('make'), raw_listing.get('model'))
        
        # Clean other fields
        cleaned_listing = {
            'listing_id': raw_listing.get('listing_id'),
            'source': raw_listing.get('source'),
            'url': raw_listing.get('url'),
            'title': raw_listing.get('title'),
            'price': self._clean_price(raw_listing.get('price')),
            'location': self._normalize_location(raw_listing.get('location')),
            'posting_date': raw_listing.get('posting_date'),
            'description': raw_listing.get('description'),
            'make': make,
            'model': model,
            'year': self._clean_year(raw_listing.get('year')),
            'mileage': self._clean_mileage(raw_listing.get('mileage')),
            'condition': raw_listing.get('condition'),
            'cylinders': raw_listing.get('cylinders'),
            'drive': raw_listing.get('drive'),
            'fuel': raw_listing.get('fuel'),
            'paint_color': raw_listing.get('paint_color'),
            'title_status': raw_listing.get('title_status'),
            'transmission': raw_listing.get('transmission'),
            'type': raw_listing.get('type')
        }
        
        return cleaned_listing
    
    def process_listing(self, mongo_listing: Dict[str, Any], historical: bool = False) -> bool:
        """Process a single listing from MongoDB to PostgreSQL."""
        try:
            # First clean the listing data
            cleaned_listing = self.clean_listing(mongo_listing)
            if not cleaned_listing:
                self.logger.warning(f"Failed to clean listing: {mongo_listing.get('listing_id')}")
                return False
            
            # Prepare image data
            image_data = self._prepare_image_data(mongo_listing)
            
            # Determine data tier based on image availability
            # Tier 1: Has images with local paths
            # Tier 2: Historical data without local images
            data_tier = 2 if historical or not any(img.get('path') for img in image_data) else 1
                
            # Insert into PostgreSQL
            listing_id = self.postgres_db.insert_processed_listing(cleaned_listing, data_tier)
            if not listing_id:
                self.logger.warning(f"Failed to insert cleaned listing into PostgreSQL: {cleaned_listing.get('listing_id')}")
                return False
                
            # Only process images for tier 1 listings (with local paths)
            if data_tier == 1 and image_data:
                image_ids = self.postgres_db.insert_images(listing_id, image_data)
                self.logger.info(f"Successfully processed listing {mongo_listing.get('listing_id')} with {len(image_ids)} images")
            else:
                self.logger.info(f"Processed historical listing {mongo_listing.get('listing_id')} (tier {data_tier}, no images)")
            
            # Mark as processed in MongoDB
            self.mongo_db.mark_listing_as_processed(mongo_listing.get('listing_id'), historical=historical)
            
            return True
        except Exception as e:
            self.logger.error(f"Error processing listing {mongo_listing.get('listing_id')}: {str(e)}")
            return False
    
    def process_batch(self, batch_size: int = 100, days_back: int = 30, include_historical: bool = False) -> Tuple[int, int]:
        """Process a batch of listings from MongoDB."""
        # Get regular listings from MongoDB
        mongo_listings = self.mongo_db.get_recent_listings(days_back, batch_size)
        
        success_count = 0
        failure_count = 0
        
        # Process regular listings first
        for listing in mongo_listings:
            if self.process_listing(listing):
                success_count += 1
            else:
                failure_count += 1
        
        # Process historical listings if requested
        if include_historical:
            historical_batch_size = batch_size - len(mongo_listings)
            if historical_batch_size > 0:
                historical_days = max(days_back, 365)  # Look at least a year back for historical
                historical_listings = self.mongo_db.get_historical_listings(historical_days, historical_batch_size)
                
                self.logger.info(f"Processing {len(historical_listings)} historical listings")
                
                for listing in historical_listings:
                    if self.process_listing(listing, historical=True):
                        success_count += 1
                    else:
                        failure_count += 1
        
        self.logger.info(f"Batch processing complete. Successes: {success_count}, Failures: {failure_count}")
        return success_count, failure_count 