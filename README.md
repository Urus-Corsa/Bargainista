# Bargainista - Intelligent Car Bargain Finder

Bargainista is a sophisticated web scraping and analytics system designed to help users find the best car deals on Craigslist by analyzing pricing, vehicle details, and market dynamics.

## Architecture

The system is built with a modular architecture that separates concerns and allows for scalable processing:

### Data Collection

- **Web Scraping**: Extracts car listings from Craigslist using Scrapy
- **Image Downloading**: Downloads images for visual inspection and analysis
- **Raw Storage**: Stores raw listing data in MongoDB for archival purposes

### Data Processing

- **Data Cleaning**: Normalizes and cleans extracted data
- **Classification**: Processes listings into Tier 1 (with images) and Tier 2 (without images)
- **Structured Storage**: Stores processed data in PostgreSQL with proper relational structure

### Market Intelligence

- **Listing Lifecycle Tracking**: Monitors listings over time to detect status changes and price adjustments
- **Market Velocity Analysis**: Calculates how quickly vehicles sell and pricing patterns
- **Bargain Detection**: Identifies listings priced below market value

## Key Components

### Database Structure

- **MongoDB**: Stores raw, unprocessed listings as flexible documents
- **PostgreSQL**: Stores normalized, processed data with these key tables:
  - `processed_listings`: Clean, normalized listing data
  - `manufacturers` & `models`: Reference tables for vehicle information
  - `images`: References to downloaded images
  - `listing_history`: Timeline of listing changes
  - `market_velocity`: Aggregated market metrics

### Modules

1. **Scrapers**: Collects data from Craigslist
2. **Data Processing**: Cleans and normalizes data
3. **Listing Tracker**: Monitors listing lifecycle and market dynamics
4. **Image Analysis**: (Planned) Detects vehicle damage from images

## Features

### Available Now

- **Multi-tier Data Processing**: Handles both listings with and without images
- **Historical Data Analysis**: Maintains valuable pricing data even after listings expire
- **Listing Lifecycle Tracking**: Monitors live listings for status changes and price drops
- **Market Velocity Metrics**: Calculates time-to-sell and price reduction patterns

### Planned

- **Damage Detection**: Computer vision model to identify vehicle damage from images
- **Price Prediction**: Machine learning model to predict fair market value
- **Bargain Alerting**: Notification system for exceptional deals
- **Web Interface**: User-friendly frontend for browsing deals

## Usage

### Data Collection

```bash
python -m main
```

### Data Processing

```bash
# Process recent listings (with images)
python -m data_processing.data_processor

# Include historical data without images
python -m data_processing.data_processor --include-historical

# Run in continuous mode
python -m data_processing.data_processor --continuous --interval 300
```

### Listing Tracker

```bash
# Track listing status and price changes
python -m data_processing.listing_tracker

# Run in continuous mode with market metrics calculation
python -m data_processing.listing_tracker --continuous --calculate-metrics

# Configure threading for improved performance
python -m data_processing.listing_tracker --parallel --max-workers 10
```

## Requirements

- Python 3.8+
- MongoDB
- PostgreSQL
- Dependencies listed in requirements.txt
