import os
import logging
import argparse
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from database.postgres import PostgresDB
from database.mongo import Database
from bs4 import BeautifulSoup
import re
import concurrent.futures
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('listing_tracker.log')
    ]
)
logger = logging.getLogger(__name__)

class ListingTracker:
    """Tracks listing lifecycles and calculates market velocity metrics."""
    
    def __init__(self, postgres_db: PostgresDB, mongo_db: Database = None):
        """Initialize with database connections."""
        self.postgres_db = postgres_db
        self.mongo_db = mongo_db
        self.logger = logging.getLogger(__name__)
        
        # User agents to rotate to avoid being blocked
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.93 Safari/537.36'
        ]
    
    def _get_random_user_agent(self) -> str:
        """Get a random user agent to avoid detection."""
        return random.choice(self.user_agents)
    
    def _check_listing_availability(self, url: str) -> Tuple[str, Optional[int]]:
        """
        Check if a listing is still available and detect price changes.
        
        Returns:
            tuple: (status, current_price)
                status: 'active', 'removed', 'expired', or 'error'
                current_price: int or None if not found/applicable
        """
        try:
            headers = {
                'User-Agent': self._get_random_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            # Check if the page has been flagged as removed or expired
            if response.status_code == 404:
                return 'removed', None
                
            # Check for common phrases in expired/removed listings
            if any(phrase in response.text.lower() for phrase in [
                'this posting has expired',
                'this posting has been deleted by its author',
                'this posting has been flagged for removal',
                'this posting has been removed'
            ]):
                return 'removed', None
                
            # If we got here, the listing is likely still active
            # Let's try to extract the current price
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to find the price element - Craigslist specific
            price_tag = soup.select_one('span.price')
            
            current_price = None
            if price_tag:
                price_text = price_tag.text.strip()
                # Extract numeric value from price (remove currency symbol, commas, etc.)
                price_match = re.search(r'\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', price_text)
                if price_match:
                    # Convert price to integer (remove commas and convert to cents)
                    current_price = int(price_match.group(1).replace(',', ''))
            
            return 'active', current_price
            
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Failed to check listing {url}: {str(e)}")
            return 'error', None
            
        except Exception as e:
            self.logger.error(f"Unexpected error checking listing {url}: {str(e)}")
            return 'error', None
    
    def process_listing(self, listing: Dict[str, Any]) -> bool:
        """
        Process a single listing to check its status and update records.
        
        Args:
            listing: A dictionary containing listing data (id, url, price, etc.)
            
        Returns:
            bool: True if processing was successful, False otherwise
        """
        try:
            listing_id = listing['id']
            original_id = listing['original_id']
            url = listing['url']
            current_db_price = listing['price']
            
            # Calculate days active based on first check date
            days_active = 0
            if listing.get('first_check_date'):
                first_check = listing['first_check_date']
                days_active = (datetime.now() - first_check).days
            
            # Check listing availability
            status, online_price = self._check_listing_availability(url)
            
            # Determine the type of change that occurred
            change_type = 'check'  # Default: just a regular check
            
            # Handle price changes and status updates
            if status == 'active':
                if online_price and online_price != current_db_price:
                    change_type = 'price_change'
                    
                    # Calculate price drop details if price decreased
                    price_drops = listing.get('price_drops', 0)
                    total_price_drop_amount = listing.get('total_price_drop_amount', 0)
                    
                    if online_price < current_db_price:
                        price_drops += 1
                        price_drop_amount = current_db_price - online_price
                        total_price_drop_amount += price_drop_amount
                        
                        change_type = 'price_drop'
                        self.logger.info(f"Price drop detected for listing {listing_id}: ${current_db_price} -> ${online_price} (-${price_drop_amount})")
                    else:
                        change_type = 'price_increase'
                        self.logger.info(f"Price increase detected for listing {listing_id}: ${current_db_price} -> ${online_price}")
                    
                    # Update listing in PostgreSQL
                    self.postgres_db.update_listing_status(
                        listing_id=listing_id, 
                        status=status,
                        price=online_price,
                        days_active=days_active,
                        price_drops=price_drops,
                        total_drop=total_price_drop_amount
                    )
                else:
                    # No price change, just update check date and days active
                    self.postgres_db.update_listing_status(
                        listing_id=listing_id,
                        status=status,
                        days_active=days_active
                    )
            elif status == 'removed':
                change_type = 'removal'
                now = datetime.now()
                
                # Update listing as removed
                self.postgres_db.update_listing_status(
                    listing_id=listing_id,
                    status=status,
                    removed_date=now,
                    days_active=days_active
                )
                
                self.logger.info(f"Listing {listing_id} marked as removed after {days_active} days")
            
            # Add a history record for this check
            details = {
                'url': url,
                'original_id': original_id,
                'online_price': online_price,
                'db_price': current_db_price
            }
            
            self.postgres_db.insert_listing_history(
                listing_id=listing_id,
                status=status,
                price=online_price if online_price else current_db_price,
                change_type=change_type,
                previous_price=current_db_price if change_type in ['price_change', 'price_drop', 'price_increase'] else None,
                days_active=days_active,
                details=details
            )
            
            # Also update the listing JSON history
            history_entry = {
                'date': datetime.now().isoformat(),
                'status': status,
                'price': online_price if online_price else current_db_price,
                'change_type': change_type,
                'days_active': days_active
            }
            self.postgres_db.update_listing_status_history(listing_id, history_entry)
            
            # If we have a MongoDB connection, update the original document as well
            if self.mongo_db and original_id:
                mongo_update = {
                    'status': status,
                    'last_check_date': datetime.now(),
                    'days_active': days_active
                }
                
                if status == 'removed':
                    mongo_update['removed_date'] = datetime.now()
                
                if online_price and online_price != current_db_price:
                    mongo_update['current_price'] = online_price
                
                self.mongo_db.update_listing(original_id, mongo_update)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error processing listing {listing.get('id')}: {str(e)}")
            return False
    
    def check_listings_batch(self, batch_size: int = 50, max_age_days: int = 30, 
                            parallel: bool = True, max_workers: int = 5) -> Tuple[int, int]:
        """
        Check the status of a batch of active listings.
        
        Args:
            batch_size: Number of listings to check in this batch
            max_age_days: Only check listings that were first seen within this timeframe
            parallel: Whether to process listings in parallel
            max_workers: Maximum number of parallel workers if parallel is True
            
        Returns:
            tuple: (success_count, failure_count)
        """
        # Get a batch of listings to check
        listings = self.postgres_db.get_listings_to_check(max_age_days, batch_size)
        
        if not listings:
            self.logger.info("No listings to check")
            return 0, 0
            
        self.logger.info(f"Checking status of {len(listings)} listings...")
        
        success_count = 0
        failure_count = 0
        
        if parallel and len(listings) > 1:
            # Process listings in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_listing = {executor.submit(self.process_listing, listing): listing for listing in listings}
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_listing):
                    listing = future_to_listing[future]
                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                        else:
                            failure_count += 1
                    except Exception as e:
                        self.logger.error(f"Error processing listing {listing.get('id')}: {str(e)}")
                        failure_count += 1
        else:
            # Process listings sequentially
            for listing in listings:
                if self.process_listing(listing):
                    success_count += 1
                else:
                    failure_count += 1
                    
                # Add a small delay to avoid being rate-limited
                time.sleep(random.uniform(1.0, 2.0))
        
        self.logger.info(f"Batch complete. Processed {len(listings)} listings: {success_count} successes, {failure_count} failures")
        return success_count, failure_count
    
    def calculate_market_velocity(self) -> bool:
        """
        Calculate and store market velocity metrics.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Calculate metrics for all makes/models
            self.postgres_db.calculate_and_store_market_velocity()
            return True
        except Exception as e:
            self.logger.error(f"Error calculating market velocity: {str(e)}")
            return False

def setup_databases():
    """Set up connections to both databases."""
    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()
    
    # PostgreSQL connection
    pg_params = {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': os.getenv('POSTGRES_PORT', 5432),
        'dbname': os.getenv('POSTGRES_DB', 'bargainista'),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', ''),
    }
    postgres_db = PostgresDB(pg_params)
    
    # MongoDB connection (optional)
    mongo_db = None
    mongo_uri = os.getenv('MONGODB_CONNECTION')
    if mongo_uri:
        mongo_db = Database(mongo_uri)
    
    return postgres_db, mongo_db

def run_tracker(batch_size: int, max_age_days: int, parallel: bool, max_workers: int,
              continuous: bool, interval: int, calculate_metrics: bool):
    """Run the listing tracker with specified parameters."""
    try:
        # Set up database connections
        postgres_db, mongo_db = setup_databases()
        
        # Create tracker
        tracker = ListingTracker(postgres_db, mongo_db)
        
        if continuous:
            logger.info(f"Starting continuous tracking mode with interval {interval} seconds")
            
            metrics_counter = 0  # Counter to track when to calculate metrics
            
            while True:
                try:
                    # Check a batch of listings
                    success_count, failure_count = tracker.check_listings_batch(
                        batch_size, max_age_days, parallel, max_workers
                    )
                    
                    # Calculate metrics periodically (every 5 batches by default)
                    metrics_counter += 1
                    if calculate_metrics and metrics_counter >= 5:
                        logger.info("Calculating market velocity metrics...")
                        tracker.calculate_market_velocity()
                        metrics_counter = 0
                    
                    # Sleep before next batch
                    logger.info(f"Sleeping for {interval} seconds before next batch")
                    time.sleep(interval)
                    
                except Exception as e:
                    logger.error(f"Error during batch processing: {str(e)}")
                    logger.info(f"Sleeping for {interval} seconds before retry")
                    time.sleep(interval)
        else:
            # Process a single batch and exit
            logger.info(f"Processing a single batch (size: {batch_size}, max age: {max_age_days} days)")
            
            success_count, failure_count = tracker.check_listings_batch(
                batch_size, max_age_days, parallel, max_workers
            )
            
            # Calculate metrics if requested
            if calculate_metrics:
                logger.info("Calculating market velocity metrics...")
                tracker.calculate_market_velocity()
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1
    
    finally:
        # Close database connections if they exist
        if 'postgres_db' in locals():
            postgres_db.close()
        
        logger.info("Listing tracker completed")
    
    return 0

def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(description='Track car listing lifecycles and calculate market metrics')
    parser.add_argument('--batch-size', type=int, default=50, help='Number of listings to check in one batch')
    parser.add_argument('--max-age', type=int, default=30, help='Maximum age in days of listings to check')
    parser.add_argument('--parallel', action='store_true', help='Process listings in parallel')
    parser.add_argument('--max-workers', type=int, default=5, help='Maximum number of parallel workers')
    parser.add_argument('--continuous', action='store_true', help='Run in continuous mode')
    parser.add_argument('--interval', type=int, default=3600, help='Sleep interval in seconds between batches in continuous mode')
    parser.add_argument('--calculate-metrics', action='store_true', help='Calculate market velocity metrics')
    
    args = parser.parse_args()
    
    return run_tracker(
        args.batch_size,
        args.max_age,
        args.parallel,
        args.max_workers,
        args.continuous,
        args.interval,
        args.calculate_metrics
    )

if __name__ == '__main__':
    exit_code = main()
    exit(exit_code) 