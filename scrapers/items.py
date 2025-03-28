import scrapy
from itemloaders.processors import TakeFirst, MapCompose, Join
from w3lib.html import remove_tags
import re

def extract_price(value):
    if value:
        match = re.search(r'\$?(\d+(?:,\d+)*)', value)
        if match:
            return int(match.group(1).replace(',', ''))
    return None

def extract_year(value):
    if value:
        match = re.search(r'(\d{4})', value)
        if match:
            return int(match.group(1)) 
    return None

class CraigslistCarItem(scrapy.Item):
    source = scrapy.Field(output_processor=TakeFirst())
    listing_id = scrapy.Field(output_processor=TakeFirst())
    url = scrapy.Field(output_processor=TakeFirst())
    title = scrapy.Field(input_processor=MapCompose(str.strip, remove_tags), output_processor=TakeFirst())
    price = scrapy.Field(input_processor=MapCompose(extract_price), output_processor=TakeFirst())
    location = scrapy.Field(
        input_processor=MapCompose(str.strip, remove_tags),
    )
    posting_date = scrapy.Field(output_processor=TakeFirst())
    description = scrapy.Field(
        input_processor=MapCompose(str.strip, remove_tags),
        output_processor=Join(' ') 
    )
    make = scrapy.Field(input_processor=MapCompose(str.strip, remove_tags), output_processor=TakeFirst())
    model = scrapy.Field(input_processor=MapCompose(str.strip, remove_tags), output_processor=TakeFirst())
    year = scrapy.Field(input_processor=MapCompose(extract_year), output_processor=TakeFirst())
    mileage = scrapy.Field(output_processor=TakeFirst())
    image_urls = scrapy.Field()
    
    condition = scrapy.Field(output_processor=TakeFirst())
    cylinders = scrapy.Field(output_processor=TakeFirst())
    drive = scrapy.Field(output_processor=TakeFirst())
    fuel = scrapy.Field(output_processor=TakeFirst())
    mileage = scrapy.Field(
        input_processor=MapCompose(lambda x: int(x.replace(',', '')) if x else None),
        output_processor=TakeFirst()
    )
    paint_color = scrapy.Field(output_processor=TakeFirst())
    title_status = scrapy.Field(output_processor=TakeFirst())
    transmission = scrapy.Field(output_processor=TakeFirst())
    type = scrapy.Field(output_processor=TakeFirst())