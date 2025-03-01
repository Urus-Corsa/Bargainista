import scrapy
from scrapy.loader import ItemLoader
from datetime import datetime
import re
import json
import logging
from ..items import CraigslistCarItem

class CraigslistSpider(scrapy.Spider):
    name = 'craigslist'
    
    def __init__(self, locations=None, *args, **kwargs):
        super(CraigslistSpider, self).__init__(*args, **kwargs)
        self.locations = locations.split(',') if locations else ['sfbay']
        self.start_urls = [f'https://{location}.craigslist.org/search/cta?purveyor=owner' for location in self.locations]

    def start_requests(self):
        for url in self.start_urls:
            self.logger.info(f"Making request to: {url}")
            yield scrapy.Request(
                url,
                callback=self.parse,
                dont_filter=True,
                headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
            )

    def parse(self, response):
        self.logger.info(f"Parsing response from: {response.url}")
        
        self.logger.debug("Full HTML structure:")
        # self.logger.debug(response.text[:2000])
        f = open("res.txt", "w")
        f.write(response.text)
        f.close()
        
        listings = response.css('body[class="no-js search"]').css('ol[class="cl-static-search-results"]').css('li[class="cl-static-search-result"]')
        self.logger.debug(f"Listings found: {bool(listings)}")
        if not listings:
            raise Exception(f"{self.start_urls} contains no listing with the defined format")

        for listing in listings:
            link = listing.css('::attr(href)').get()
            if not link:
                link = listing.xpath('@href').get()
                
            if link:
                self.logger.debug(f"Found listing link: {link}")
                yield response.follow(link, self.parse_listing)
        
        #pagination can be enabled later
        # next_page = response.css('a.next, a.nextpage, a[title*="next"], button.next-page::attr(href)').get()
        # if next_page:
        #     self.logger.info(f"Following next page: {next_page}")
        #     yield response.follow(next_page, self.parse)

    def _process_location(self, location_text: str) -> list:
        if not location_text:
            return []
        clean_text = location_text.strip('() ')
        locations = [
            loc.strip().lower()
            for loc in re.split(r'/|,', clean_text)
            if loc.strip()
        ]
        return locations

    def parse_listing(self, response):
        self.logger.info(f"Parsing listing: {response.url}")
        loader = ItemLoader(item=CraigslistCarItem(), response=response)
        
        loader.add_value('source', 'craigslist')
        loader.add_value('url', response.url)
        
        #post id coming form listing
        post_id_match = re.search(r'/(\d+)\.html', response.url)
        if post_id_match:
            loader.add_value('listing_id', post_id_match.group(1))
        
        loader.add_css('title', 'span#titletextonly')
        loader.add_css('price', 'span.price')
        
        #location
        location_text = response.xpath('//span[@class="postingtitletext"]/span[last()]/text()').get()
        if location_text:
            locations = self._process_location(location_text)
            loader.add_value('location', locations)
        
        date_str = response.css('p.postinginfo time::attr(datetime)').get()
        if date_str:
            try:
                if len(date_str) == 24:  
                    offset = date_str[-5:]
                    date_str = f"{date_str[:-5]}{offset[:3]}:{offset[3:]}"
                posting_date = datetime.fromisoformat(date_str)
                loader.add_value('posting_date', posting_date)
            except ValueError as e:
                self.logger.error(f"Failed to parse date {date_str}: {e}")
        
        #description
        # loader.add_css('description', '#postingbody')
        description = response.xpath('//section[@id="postingbody"]/text() | //section[@id="postingbody"]/br/following-sibling::text()').getall()
        if description:
            #no empty strings
            cleaned_description = ' '.join(text.strip() for text in description if text.strip())
            loader.add_value('description', cleaned_description)
        
        #attributes from first attrgroup (year, make, model)
        year_div = response.css('div.attrgroup div.attr.important')
        if year_div:
            year = year_div.css('span.year::text').get()
            if year:
                loader.add_value('year', year.strip())
            
            make_model = year_div.css('span.makemodel a::text').get()
            if make_model:
                #c.g. current format "honda civic hybrid" -> ["honda", "civic hybrid"])
                parts = make_model.strip().lower().split(' ', 1)
                if len(parts) >= 2:
                    loader.add_value('make', parts[0])
                    loader.add_value('model', parts[1])

        #second attrgroup div containing attributes
        attrs_div = response.css('div.mapAndAttrs div.attrgroup')[1]
        
        #other attributes from second attrgroup
        attr_mapping = {
            'condition': 'div.attr.condition span.valu a::text',
            'cylinders': 'div.attr.auto_cylinders span.valu a::text',
            'drive': 'div.attr.auto_drivetrain span.valu a::text',
            'fuel': 'div.attr.auto_fuel_type span.valu a::text',
            'mileage': 'div.attr.auto_miles span.valu::text',
            'paint_color': 'div.attr.auto_paint span.valu a::text',
            'title_status': 'div.attr.auto_title_status span.valu a::text',
            'transmission': 'div.attr.auto_transmission span.valu a::text',
            'type': 'div.attr.auto_bodytype span.valu a::text'
        }

        for field, selector in attr_mapping.items():
            #attrs_div as the base for css selectors
            value = attrs_div.css(selector).get()
            if value:
                value = value.strip()
                self.logger.debug(f"Found {field}: {value}")
                loader.add_value(field, value)

        #get image URLs
        image_urls = []
        image_json = response.css('script:contains("imgList")').re_first(r'var imgList = (\[.+?\]);')
        if image_json:
            import json
            try:
                image_data = json.loads(image_json)
                for img in image_data:
                    if 'url' in img:
                        image_urls.append(img['url'])
            except json.JSONDecodeError:
                self.logger.error(f"Failed to parse image JSON: {image_json}")
        
        loader.add_value('image_urls', image_urls)
        
        yield loader.load_item()