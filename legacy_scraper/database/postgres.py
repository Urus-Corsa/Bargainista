import logging
import os
from typing import List, Dict, Any, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2 import pool
from datetime import datetime, timedelta

class PostgresDB:
    def __init__(self, db_params: Dict[str, str]):
        """Initialize PostgreSQL connection pool."""
        self.logger = logging.getLogger(__name__)
        self.connection_pool = pool.SimpleConnectionPool(1, 10, **db_params)
        self.logger.info("PostgreSQL connection pool initialized")
        
        # Create necessary tables if they don't exist
        self._create_tables()
    
    def _get_conn(self):
        """Get a connection from the pool."""
        return self.connection_pool.getconn()
    
    def _release_conn(self, conn):
        """Release a connection back to the pool."""
        self.connection_pool.putconn(conn)
    
    def _execute_query(self, query: str, params: tuple = None, fetch: bool = False):
        """Execute a query and optionally fetch results."""
        conn = None
        cursor = None
        try:
            conn = self._get_conn()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            
            if fetch:
                result = cursor.fetchall()
                return result
            
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            self.logger.error(f"Database error: {str(e)}")
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._release_conn(conn)
    
    def _create_tables(self):
        """Create the necessary tables if they don't exist."""
        create_tables_query = """
        -- Manufacturers (car makes) table
        CREATE TABLE IF NOT EXISTS manufacturers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL
        );
        
        -- Car models table
        CREATE TABLE IF NOT EXISTS models (
            id SERIAL PRIMARY KEY,
            manufacturer_id INTEGER REFERENCES manufacturers(id),
            name VARCHAR(100) NOT NULL,
            UNIQUE (manufacturer_id, name)
        );
        
        -- Listings table - cleaned and normalized data
        CREATE TABLE IF NOT EXISTS processed_listings (
            id SERIAL PRIMARY KEY,
            original_id VARCHAR(50) UNIQUE NOT NULL,
            source VARCHAR(50) NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            location TEXT,
            posting_date TIMESTAMP,
            description TEXT,
            make_id INTEGER REFERENCES manufacturers(id),
            model_id INTEGER REFERENCES models(id),
            year INTEGER,
            mileage INTEGER,
            condition VARCHAR(50),
            cylinders VARCHAR(20),
            drive VARCHAR(20),
            fuel VARCHAR(20),
            paint_color VARCHAR(30),
            title_status VARCHAR(30),
            transmission VARCHAR(20),
            body_type VARCHAR(30),
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bargain_score DECIMAL(5,2),
            price_analysis JSONB,
            data_tier INTEGER DEFAULT 1,
            
            -- Listing lifecycle tracking fields
            status VARCHAR(20) DEFAULT 'active',
            last_check_date TIMESTAMP,
            first_check_date TIMESTAMP,
            removed_date TIMESTAMP,
            days_active INTEGER,
            price_drops INTEGER DEFAULT 0,
            total_price_drop_amount INTEGER DEFAULT 0,
            status_history JSONB DEFAULT '[]'::jsonb
        );
        
        -- Images table
        CREATE TABLE IF NOT EXISTS images (
            id SERIAL PRIMARY KEY,
            listing_id INTEGER REFERENCES processed_listings(id),
            original_url TEXT,
            local_path TEXT,
            is_processed BOOLEAN DEFAULT FALSE,
            damage_detected BOOLEAN DEFAULT FALSE,
            damage_analysis JSONB,
            processing_date TIMESTAMP
        );
        
        -- Listing history table for detailed timeline
        CREATE TABLE IF NOT EXISTS listing_history (
            id SERIAL PRIMARY KEY,
            listing_id INTEGER REFERENCES processed_listings(id),
            check_date TIMESTAMP NOT NULL,
            status VARCHAR(20) NOT NULL,
            price INTEGER,
            change_type VARCHAR(20),
            previous_price INTEGER,
            days_active INTEGER,
            details JSONB
        );
        
        -- Market velocity metrics table
        CREATE TABLE IF NOT EXISTS market_velocity (
            id SERIAL PRIMARY KEY,
            make_id INTEGER REFERENCES manufacturers(id),
            model_id INTEGER REFERENCES models(id),
            year_range VARCHAR(20),
            price_range VARCHAR(30),
            avg_days_to_removal DECIMAL(5,2),
            median_days_to_removal DECIMAL(5,2),
            price_drop_frequency DECIMAL(5,2),
            avg_price_drop_percent DECIMAL(5,2),
            sample_size INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confidence_score DECIMAL(5,2)
        );
        
        -- Create indexes for performance
        CREATE INDEX IF NOT EXISTS idx_make_model ON processed_listings(make_id, model_id);
        CREATE INDEX IF NOT EXISTS idx_year ON processed_listings(year);
        CREATE INDEX IF NOT EXISTS idx_bargain_score ON processed_listings(bargain_score);
        CREATE INDEX IF NOT EXISTS idx_data_tier ON processed_listings(data_tier);
        CREATE INDEX IF NOT EXISTS idx_listing_status ON processed_listings(status);
        CREATE INDEX IF NOT EXISTS idx_listing_active_days ON processed_listings(days_active);
        CREATE INDEX IF NOT EXISTS idx_history_listing_id ON listing_history(listing_id);
        CREATE INDEX IF NOT EXISTS idx_history_check_date ON listing_history(check_date);
        CREATE INDEX IF NOT EXISTS idx_velocity_make_model ON market_velocity(make_id, model_id);
        """
        self._execute_query(create_tables_query)
        self.logger.info("Database tables created or confirmed to exist")
    
    def get_or_create_manufacturer(self, make_name: str) -> int:
        """Get or create a manufacturer record and return its ID."""
        if not make_name:
            return None
            
        # Normalize the manufacturer name
        make_name = make_name.strip().lower()
        
        # First try to get the existing record
        query = "SELECT id FROM manufacturers WHERE name = %s"
        result = self._execute_query(query, (make_name,), fetch=True)
        
        if result:
            return result[0]['id']
        
        # Create new record if it doesn't exist
        insert_query = "INSERT INTO manufacturers (name) VALUES (%s) RETURNING id"
        result = self._execute_query(insert_query, (make_name,), fetch=True)
        self.logger.info(f"Created new manufacturer: {make_name}")
        return result[0]['id']
    
    def get_or_create_model(self, make_id: int, model_name: str) -> int:
        """Get or create a model record and return its ID."""
        if not make_id or not model_name:
            return None
            
        # Normalize the model name
        model_name = model_name.strip().lower()
        
        # First try to get the existing record
        query = "SELECT id FROM models WHERE manufacturer_id = %s AND name = %s"
        result = self._execute_query(query, (make_id, model_name), fetch=True)
        
        if result:
            return result[0]['id']
        
        # Create new record if it doesn't exist
        insert_query = "INSERT INTO models (manufacturer_id, name) VALUES (%s, %s) RETURNING id"
        result = self._execute_query(insert_query, (make_id, model_name), fetch=True)
        self.logger.info(f"Created new model: {model_name} for manufacturer ID: {make_id}")
        return result[0]['id']
    
    def insert_processed_listing(self, listing_data: Dict[str, Any], data_tier: int = 1) -> int:
        """Insert a processed listing and return its ID."""
        # Get or create make and model records
        make_id = self.get_or_create_manufacturer(listing_data.get('make'))
        model_id = None
        if make_id and listing_data.get('model'):
            model_id = self.get_or_create_model(make_id, listing_data.get('model'))
        
        # Prepare the insert query
        query = """
        INSERT INTO processed_listings (
            original_id, source, url, title, price, location, 
            posting_date, description, make_id, model_id, year, mileage,
            condition, cylinders, drive, fuel, paint_color, title_status,
            transmission, body_type, data_tier, first_check_date, status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
        """
        
        now = datetime.now()
        
        params = (
            listing_data.get('listing_id'),
            listing_data.get('source'),
            listing_data.get('url'),
            listing_data.get('title'),
            listing_data.get('price'),
            listing_data.get('location')[0] if isinstance(listing_data.get('location'), list) else listing_data.get('location'),
            listing_data.get('posting_date'),
            listing_data.get('description'),
            make_id,
            model_id,
            listing_data.get('year'),
            listing_data.get('mileage'),
            listing_data.get('condition'),
            listing_data.get('cylinders'),
            listing_data.get('drive'),
            listing_data.get('fuel'),
            listing_data.get('paint_color'),
            listing_data.get('title_status'),
            listing_data.get('transmission'),
            listing_data.get('type'),  # This maps to body_type in our schema
            data_tier,
            now,
            'active'
        )
        
        result = self._execute_query(query, params, fetch=True)
        listing_id = result[0]['id']
        self.logger.info(f"Inserted processed listing with ID: {listing_id}, original ID: {listing_data.get('listing_id')}, tier: {data_tier}")
        
        # Also insert the initial history record
        if listing_id:
            self.insert_listing_history(
                listing_id=listing_id,
                status='active',
                price=listing_data.get('price'),
                change_type='initial',
                previous_price=None,
                days_active=0,
                details={'initial_import': True}
            )
        
        return listing_id
    
    def insert_images(self, listing_id: int, image_data: List[Dict[str, str]]) -> List[int]:
        """Insert image records for a processed listing."""
        if not image_data:
            return []
            
        image_ids = []
        for image in image_data:
            query = """
            INSERT INTO images (listing_id, original_url, local_path)
            VALUES (%s, %s, %s) RETURNING id
            """
            params = (listing_id, image.get('url'), image.get('path'))
            result = self._execute_query(query, params, fetch=True)
            image_ids.append(result[0]['id'])
        
        self.logger.info(f"Inserted {len(image_ids)} images for listing ID: {listing_id}")
        return image_ids
    
    def update_bargain_analysis(self, listing_id: int, bargain_score: float, price_analysis: Dict) -> bool:
        """Update a listing with bargain analysis data."""
        query = """
        UPDATE processed_listings
        SET bargain_score = %s, price_analysis = %s
        WHERE id = %s
        """
        params = (bargain_score, Json(price_analysis), listing_id)
        affected_rows = self._execute_query(query, params)
        success = affected_rows > 0
        self.logger.info(f"Updated bargain analysis for listing ID: {listing_id}, success: {success}")
        return success
    
    def update_image_processing(self, image_id: int, damage_detected: bool, damage_analysis: Dict) -> bool:
        """Update an image record with damage analysis results."""
        query = """
        UPDATE images
        SET is_processed = TRUE, damage_detected = %s, damage_analysis = %s, processing_date = CURRENT_TIMESTAMP
        WHERE id = %s
        """
        params = (damage_detected, Json(damage_analysis), image_id)
        affected_rows = self._execute_query(query, params)
        success = affected_rows > 0
        self.logger.info(f"Updated damage analysis for image ID: {image_id}, success: {success}")
        return success
    
    def get_unprocessed_images(self, limit: int = 100) -> List[Dict]:
        """Get images that haven't been processed yet."""
        query = """
        SELECT i.id, i.listing_id, i.local_path, i.original_url, 
               p.make_id, p.model_id, p.year
        FROM images i
        JOIN processed_listings p ON i.listing_id = p.id
        WHERE i.is_processed = FALSE
        LIMIT %s
        """
        result = self._execute_query(query, (limit,), fetch=True)
        self.logger.info(f"Retrieved {len(result)} unprocessed images")
        return result
    
    def get_listings_to_check(self, max_age_days: int = 30, limit: int = 100) -> List[Dict]:
        """Get active listings that need to be checked for status updates."""
        query = """
        SELECT p.id, p.original_id, p.url, p.price, p.make_id, p.model_id, p.year,
               p.last_check_date, p.first_check_date, p.status, p.days_active
        FROM processed_listings p
        WHERE p.status = 'active'
          AND (p.last_check_date IS NULL OR p.last_check_date < NOW() - INTERVAL '12 hours')
          AND p.processed_at > NOW() - INTERVAL '%s days'
        ORDER BY p.last_check_date ASC NULLS FIRST
        LIMIT %s
        """
        result = self._execute_query(query, (max_age_days, limit), fetch=True)
        self.logger.info(f"Retrieved {len(result)} listings to check for status updates")
        return result
    
    def update_listing_status(self, listing_id: int, status: str, price: Optional[int] = None, 
                             removed_date: Optional[datetime] = None, days_active: Optional[int] = None,
                             price_drops: Optional[int] = None, total_drop: Optional[int] = None) -> bool:
        """Update a listing's status and tracking data."""
        now = datetime.now()
        
        # Build the dynamic part of the query based on what's provided
        set_clause = "last_check_date = %s, status = %s"
        params = [now, status]
        
        if price is not None:
            set_clause += ", price = %s"
            params.append(price)
            
        if removed_date is not None:
            set_clause += ", removed_date = %s"
            params.append(removed_date)
            
        if days_active is not None:
            set_clause += ", days_active = %s"
            params.append(days_active)
            
        if price_drops is not None:
            set_clause += ", price_drops = %s"
            params.append(price_drops)
            
        if total_drop is not None:
            set_clause += ", total_price_drop_amount = %s"
            params.append(total_drop)
        
        query = f"""
        UPDATE processed_listings
        SET {set_clause}
        WHERE id = %s
        """
        params.append(listing_id)
        
        affected_rows = self._execute_query(query, tuple(params))
        success = affected_rows > 0
        self.logger.info(f"Updated listing status: id={listing_id}, status={status}, success={success}")
        return success
    
    def insert_listing_history(self, listing_id: int, status: str, price: Optional[int], 
                              change_type: str, previous_price: Optional[int] = None,
                              days_active: int = 0, details: Optional[Dict] = None) -> int:
        """Insert a record in the listing history table."""
        query = """
        INSERT INTO listing_history (
            listing_id, check_date, status, price, change_type, 
            previous_price, days_active, details
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
        """
        
        params = (
            listing_id,
            datetime.now(),
            status,
            price,
            change_type,
            previous_price,
            days_active,
            Json(details) if details else None
        )
        
        result = self._execute_query(query, params, fetch=True)
        history_id = result[0]['id']
        self.logger.debug(f"Inserted listing history: id={history_id}, listing_id={listing_id}, change={change_type}")
        return history_id
    
    def update_listing_status_history(self, listing_id: int, status_entry: Dict[str, Any]) -> bool:
        """Add an entry to the listing's status history JSON array."""
        query = """
        UPDATE processed_listings
        SET status_history = status_history || %s::jsonb
        WHERE id = %s
        """
        params = (Json(status_entry), listing_id)
        affected_rows = self._execute_query(query, params)
        success = affected_rows > 0
        return success
    
    def calculate_and_store_market_velocity(self, make_id: Optional[int] = None, 
                                          model_id: Optional[int] = None,
                                          min_sample_size: int = 10) -> bool:
        """Calculate market velocity metrics and store in the market_velocity table."""
        # This query calculates market velocity metrics for a specific make/model
        # It can be run for all makes/models by leaving parameters as None
        
        # Define the WHERE clause based on parameters
        make_model_where = ""
        params = []
        
        if make_id is not None:
            make_model_where += " AND p.make_id = %s"
            params.append(make_id)
            
        if model_id is not None:
            make_model_where += " AND p.model_id = %s"
            params.append(model_id)
        
        # Add min sample size
        params.append(min_sample_size)
        
        # Base query for velocity metrics
        query = f"""
        WITH removed_listings AS (
            SELECT 
                p.make_id,
                p.model_id,
                p.year,
                p.price,
                p.days_active,
                p.price_drops,
                p.total_price_drop_amount,
                CASE 
                    WHEN p.price > 0 AND p.total_price_drop_amount > 0 
                    THEN (p.total_price_drop_amount::float / p.price) * 100.0
                    ELSE 0 
                END as price_drop_percent
            FROM processed_listings p
            WHERE p.status = 'removed'
              AND p.days_active > 0
              AND p.days_active < 31  -- exclude outliers
              {make_model_where}
        ),
        grouped_data AS (
            SELECT
                r.make_id,
                r.model_id,
                CASE 
                    WHEN r.year < 2000 THEN 'pre-2000'
                    WHEN r.year BETWEEN 2000 AND 2010 THEN '2000-2010'
                    WHEN r.year BETWEEN 2011 AND 2015 THEN '2011-2015'
                    WHEN r.year BETWEEN 2016 AND 2020 THEN '2016-2020'
                    ELSE '2021+'
                END as year_range,
                CASE
                    WHEN r.price < 5000 THEN 'under-5k'
                    WHEN r.price BETWEEN 5000 AND 10000 THEN '5k-10k'
                    WHEN r.price BETWEEN 10001 AND 20000 THEN '10k-20k'
                    WHEN r.price BETWEEN 20001 AND 30000 THEN '20k-30k'
                    WHEN r.price BETWEEN 30001 AND 50000 THEN '30k-50k'
                    ELSE 'over-50k'
                END as price_range,
                AVG(r.days_active) as avg_days_to_removal,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.days_active) as median_days_to_removal,
                AVG(CASE WHEN r.price_drops > 0 THEN 1.0 ELSE 0.0 END) as price_drop_frequency,
                AVG(r.price_drop_percent) as avg_price_drop_percent,
                COUNT(*) as sample_size,
                -- Confidence score based on sample size
                CASE
                    WHEN COUNT(*) > 50 THEN 90.0
                    WHEN COUNT(*) > 30 THEN 80.0
                    WHEN COUNT(*) > 20 THEN 70.0
                    WHEN COUNT(*) > 10 THEN 60.0
                    ELSE 50.0
                END as confidence_score
            FROM removed_listings r
            GROUP BY 
                r.make_id, 
                r.model_id,
                year_range,
                price_range
            HAVING COUNT(*) >= %s
        )
        SELECT * FROM grouped_data
        """
        
        result = self._execute_query(query, tuple(params), fetch=True)
        
        if not result:
            self.logger.info("No sufficient market velocity data to calculate")
            return False
        
        # Insert or update the data in the market_velocity table
        for row in result:
            upsert_query = """
            INSERT INTO market_velocity (
                make_id, model_id, year_range, price_range, avg_days_to_removal,
                median_days_to_removal, price_drop_frequency, avg_price_drop_percent,
                sample_size, timestamp, confidence_score
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (make_id, model_id, year_range, price_range) 
            DO UPDATE SET
                avg_days_to_removal = EXCLUDED.avg_days_to_removal,
                median_days_to_removal = EXCLUDED.median_days_to_removal,
                price_drop_frequency = EXCLUDED.price_drop_frequency,
                avg_price_drop_percent = EXCLUDED.avg_price_drop_percent,
                sample_size = EXCLUDED.sample_size,
                timestamp = EXCLUDED.timestamp,
                confidence_score = EXCLUDED.confidence_score
            """
            
            params = (
                row['make_id'],
                row['model_id'],
                row['year_range'],
                row['price_range'],
                row['avg_days_to_removal'],
                row['median_days_to_removal'],
                row['price_drop_frequency'],
                row['avg_price_drop_percent'],
                row['sample_size'],
                datetime.now(),
                row['confidence_score']
            )
            
            self._execute_query(upsert_query, params)
            
        self.logger.info(f"Calculated and stored market velocity metrics for {len(result)} segments")
        return True
    
    def get_market_velocity_metrics(self, make_id: Optional[int] = None, 
                                 model_id: Optional[int] = None,
                                 year_range: Optional[str] = None,
                                 price_range: Optional[str] = None) -> List[Dict]:
        """Get market velocity metrics for specific criteria."""
        conditions = []
        params = []
        
        if make_id:
            conditions.append("make_id = %s")
            params.append(make_id)
            
        if model_id:
            conditions.append("model_id = %s")
            params.append(model_id)
            
        if year_range:
            conditions.append("year_range = %s")
            params.append(year_range)
            
        if price_range:
            conditions.append("price_range = %s")
            params.append(price_range)
            
        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        
        query = f"""
        SELECT 
            mv.*,
            m.name as make_name,
            md.name as model_name
        FROM market_velocity mv
        JOIN manufacturers m ON mv.make_id = m.id
        JOIN models md ON mv.model_id = md.id
        WHERE {where_clause}
        ORDER BY confidence_score DESC
        """
        
        result = self._execute_query(query, tuple(params), fetch=True)
        return result
    
    def find_listings_by_criteria(self, make_id: Optional[int] = None, model_id: Optional[int] = None, 
                                 year_range: Optional[Tuple[int, int]] = None, 
                                 min_bargain_score: Optional[float] = None,
                                 data_tier: Optional[int] = None,
                                 status: Optional[str] = None,
                                 limit: int = 50) -> List[Dict]:
        """Find listings matching the given criteria."""
        conditions = []
        params = []
        
        if make_id:
            conditions.append("make_id = %s")
            params.append(make_id)
        
        if model_id:
            conditions.append("model_id = %s")
            params.append(model_id)
        
        if year_range and len(year_range) == 2:
            conditions.append("year BETWEEN %s AND %s")
            params.extend(year_range)
        
        if min_bargain_score is not None:
            conditions.append("bargain_score >= %s")
            params.append(min_bargain_score)
            
        if data_tier is not None:
            conditions.append("data_tier = %s")
            params.append(data_tier)
            
        if status:
            conditions.append("status = %s")
            params.append(status)
        
        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        
        query = f"""
        SELECT p.*, 
               m1.name as make_name, 
               m2.name as model_name,
               (SELECT COUNT(*) FROM images i WHERE i.listing_id = p.id) as image_count,
               (SELECT COUNT(*) FROM images i WHERE i.listing_id = p.id AND i.damage_detected = TRUE) as damaged_image_count
        FROM processed_listings p
        LEFT JOIN manufacturers m1 ON p.make_id = m1.id
        LEFT JOIN models m2 ON p.model_id = m2.id
        WHERE {where_clause}
        ORDER BY p.bargain_score DESC NULLS LAST
        LIMIT %s
        """
        params.append(limit)
        
        result = self._execute_query(query, tuple(params), fetch=True)
        self.logger.info(f"Found {len(result)} listings matching criteria")
        return result
    
    def close(self):
        """Close all connections in the pool."""
        if self.connection_pool:
            self.connection_pool.closeall()
            self.logger.info("PostgreSQL connection pool closed") 