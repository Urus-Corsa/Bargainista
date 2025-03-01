'''
from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional
from datetime import datetime
from bson import ObjectId

class PyObjectId(ObjectId):
  @classmethod
  def __get_validators__(cls):
    yield cls.validate

  @classmethod
  def validate(cls, v):
    if not ObjectId.is_valid(v):
      raise ValueError("Invalid ObjectId")
    return ObjectId(v)

  @classmethod
  def __get_pydantic_json_schema__(cls, field_schema):
    field_schema.update(type="string")

class CarListing(BaseModel):
  id: Optional[PyObjectId] = Field(alias="_id")
  source: str
  listing_id: str
  url: str
  title: str
  price: Optional[float]
  location: Optional[str]
  posting_date: Optional[datetime]
  description: Optional[str]
  make: Optional[str]
  model: Optional[str]
  year: Optional[int]
  mileage: Optional[int]
  image_urls: List[str] = []
  scraped_at: datetime = Field(default_factory=datetime.now)

class Config:
  allow_population_by_field_name = True
  arbitrary_types_allowed = True
  json_encoders = {ObjectId: str}
'''
# class ImageAnalysisResult(BaseModel):
#   listing_id: str
#   image_url: HttpUrl
#   damage_detected: bool
#   damage_locations: List[str] = []
#   damage_severity: Optional[float]
#   confidence_score: float
    
# class MarketAnalysis(BaseModel):
#   listing_id: str
#   market_avg_price: float
#   price_percentile: float
#   classification: str  # "great_deal", "fair_price", "overpriced"
#   confidence_score: float
#   factors: dict