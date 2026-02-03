import os
import logging
import argparse
import time
from dotenv import load_dotenv
from database.mongo import Database
from database.postgres import PostgresDB
from data_processing.data_cleaner import DataCleaner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data_processing.log')
    ]
)
logger = logging.getLogger(__name__)

def setup_database_connections():
    """Set up connections to both databases."""
    # Load environment variables
    load_dotenv()
    
    # MongoDB connection
    mongo_uri = os.getenv('MONGODB_CONNECTION')
    if not mongo_uri:
        raise ValueError("MongoDB connection string not found in environment variables")
    mongo_db = Database(mongo_uri)
    
    # PostgreSQL connection
    pg_params = {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': os.getenv('POSTGRES_PORT', 5432),
        'dbname': os.getenv('POSTGRES_DB', 'bargainista'),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', ''),
    }
    postgres_db = PostgresDB(pg_params)
    
    return mongo_db, postgres_db

def process_data(batch_size, days_back, include_historical=False, historical_days=365, 
                continuous_mode=False, sleep_interval=300):
    """Process data from MongoDB to PostgreSQL."""
    try:
        # Set up database connections
        mongo_db, postgres_db = setup_database_connections()
        
        # Create data cleaner
        data_cleaner = DataCleaner(mongo_db, postgres_db)
        
        if continuous_mode:
            logger.info(f"Starting continuous processing mode with interval {sleep_interval} seconds")
            logger.info(f"Processing {'with' if include_historical else 'without'} historical data")
            
            while True:
                try:
                    # Process a batch
                    success_count, failure_count = data_cleaner.process_batch(
                        batch_size, 
                        days_back, 
                        include_historical=include_historical
                    )
                    
                    # Log results
                    total = success_count + failure_count
                    if total > 0:
                        logger.info(f"Processed {total} listings: {success_count} successes, {failure_count} failures")
                    else:
                        logger.info("No new listings to process")
                    
                    # Sleep before next batch
                    logger.info(f"Sleeping for {sleep_interval} seconds before next batch")
                    time.sleep(sleep_interval)
                    
                except Exception as e:
                    logger.error(f"Error during batch processing: {str(e)}")
                    logger.info(f"Sleeping for {sleep_interval} seconds before retry")
                    time.sleep(sleep_interval)
        else:
            # Process a single batch and exit
            logger.info(f"Processing a single batch (size: {batch_size}, days back: {days_back})")
            logger.info(f"Including historical data: {include_historical}")
            
            success_count, failure_count = data_cleaner.process_batch(
                batch_size, 
                days_back, 
                include_historical=include_historical
            )
            
            # Log final results
            total = success_count + failure_count
            if total > 0:
                logger.info(f"Processed {total} listings: {success_count} successes, {failure_count} failures")
            else:
                logger.info("No listings to process")
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1
    
    finally:
        # Close database connections if they exist
        if 'postgres_db' in locals():
            postgres_db.close()
        
        logger.info("Data processing completed")
    
    return 0

def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(description='Process car listings data from MongoDB to PostgreSQL')
    parser.add_argument('--batch-size', type=int, default=100, help='Number of listings to process in one batch')
    parser.add_argument('--days-back', type=int, default=30, help='How many days back to look for current listings')
    parser.add_argument('--include-historical', action='store_true', help='Process historical listings without images')
    parser.add_argument('--historical-days', type=int, default=365, help='How many days back to look for historical data')
    parser.add_argument('--continuous', action='store_true', help='Run in continuous mode, processing new data as it arrives')
    parser.add_argument('--interval', type=int, default=300, help='Sleep interval in seconds between batches in continuous mode')
    
    args = parser.parse_args()
    
    return process_data(
        args.batch_size, 
        args.days_back,
        args.include_historical,
        args.historical_days,
        args.continuous, 
        args.interval
    )

if __name__ == '__main__':
    exit_code = main()
    exit(exit_code) 