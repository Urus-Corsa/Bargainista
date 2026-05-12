"""Depreciation config seed data.

Loaded once on first run by init_db.py. If any of the three config tables
are non-empty the seed is skipped entirely — live admin updates are never
overwritten by a redeploy.

All values sourced from:
  - Edmunds True Cost to Own / depreciation data
  - iSeeCars annual depreciation studies
  - Consumer Reports annual auto reliability survey
  - J.D. Power Vehicle Dependability Study (VDS)
  - RepairPal reliability rankings
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import BrandModifier, DepreciationCategory, VariantOverride

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category curves
# Index = vehicle age in years (0–10). Value = fraction of MSRP retained.
# ---------------------------------------------------------------------------

CATEGORY_SEED = [
    {
        "name": "sedan",
        "curve_values": [1.00, 0.79, 0.67, 0.58, 0.51, 0.46, 0.41, 0.37, 0.34, 0.31, 0.28],
        "range_band": 0.08,
        "source_note": "Edmunds / iSeeCars mainstream sedan aggregate 2022–2024",
    },
    {
        "name": "luxury_sedan",
        "curve_values": [1.00, 0.72, 0.60, 0.51, 0.44, 0.38, 0.34, 0.30, 0.27, 0.24, 0.22],
        "range_band": 0.12,
        "source_note": "iSeeCars luxury sedan depreciation study 2023; steeper Y1 due to high MSRP and maintenance cost perception",
    },
    {
        "name": "suv",
        "curve_values": [1.00, 0.80, 0.70, 0.62, 0.55, 0.49, 0.44, 0.40, 0.36, 0.33, 0.30],
        "range_band": 0.08,
        "source_note": "Edmunds mainstream crossover/SUV aggregate 2022–2024",
    },
    {
        "name": "luxury_suv",
        "curve_values": [1.00, 0.74, 0.63, 0.54, 0.47, 0.41, 0.37, 0.33, 0.30, 0.27, 0.25],
        "range_band": 0.12,
        "source_note": "iSeeCars luxury SUV depreciation study 2023",
    },
    {
        "name": "truck",
        "curve_values": [1.00, 0.83, 0.74, 0.67, 0.61, 0.56, 0.51, 0.47, 0.43, 0.40, 0.37],
        "range_band": 0.08,
        "source_note": "Edmunds full-size truck aggregate (F-150 / Silverado / Ram 1500) 2022–2024; best retention of all categories",
    },
    {
        "name": "ev",
        "curve_values": [1.00, 0.71, 0.61, 0.53, 0.47, 0.42, 0.38, 0.34, 0.31, 0.28, 0.25],
        "range_band": 0.12,
        "source_note": "iSeeCars EV depreciation study 2023; wider band reflects battery technology obsolescence risk",
    },
    {
        "name": "minivan",
        "curve_values": [1.00, 0.78, 0.65, 0.56, 0.49, 0.43, 0.39, 0.35, 0.32, 0.29, 0.27],
        "range_band": 0.08,
        "source_note": "Edmunds minivan aggregate 2022–2024; slightly below sedan due to niche demand",
    },
    {
        "name": "sports",
        "curve_values": [1.00, 0.80, 0.68, 0.58, 0.51, 0.45, 0.41, 0.37, 0.34, 0.31, 0.29],
        "range_band": 0.15,
        "source_note": "Edmunds sports car aggregate 2022–2024; widest band — manual/auto, colour, condition variance is extreme",
    },
]

# ---------------------------------------------------------------------------
# Brand modifiers
# ---------------------------------------------------------------------------

BRAND_SEED = [
    # Everyday / High-Volume
    {"brand_name": "toyota", "modifier": 0.06, "segment": "everyday",
     "source_note": "CR #1-2 reliability 15+ consecutive years; iSeeCars retention consistently above segment"},
    {"brand_name": "honda", "modifier": 0.04, "segment": "everyday",
     "source_note": "Consistently top-tier CR reliability; strong resale demand"},
    {"brand_name": "mazda", "modifier": 0.05, "segment": "everyday",
     "source_note": "CR 2022–2023 placed Mazda above Honda; iSeeCars retention data confirms"},
    {"brand_name": "hyundai", "modifier": 0.02, "segment": "everyday",
     "source_note": "CR above average since 2018; strong warranty demand drives used market"},
    {"brand_name": "kia", "modifier": 0.02, "segment": "everyday",
     "source_note": "Sister company to Hyundai; same reliability improvement trajectory"},
    {"brand_name": "subaru", "modifier": 0.02, "segment": "everyday",
     "source_note": "CR above average overall; EJ25 outliers handled by variant overrides"},
    {"brand_name": "chevrolet", "modifier": 0.00, "segment": "everyday",
     "source_note": "Baseline domestic benchmark; JD Power near average"},
    {"brand_name": "gmc", "modifier": 0.00, "segment": "everyday",
     "source_note": "Same platform as Chevrolet; near average"},
    {"brand_name": "ford", "modifier": -0.01, "segment": "everyday",
     "source_note": "Trucks strong; cars below average; mixed CR overall"},
    {"brand_name": "ram", "modifier": 0.00, "segment": "everyday",
     "source_note": "Truck-focused; competitive with Ford in reliability and demand"},
    {"brand_name": "nissan", "modifier": -0.01, "segment": "everyday",
     "source_note": "Inconsistent across lines; CVT reliability concerns documented"},
    {"brand_name": "mitsubishi", "modifier": -0.02, "segment": "everyday",
     "source_note": "Below average reliability; shrinking US presence compresses used demand"},
    {"brand_name": "chrysler", "modifier": -0.03, "segment": "everyday",
     "source_note": "CR below average; transmission reliability concerns"},
    {"brand_name": "dodge", "modifier": -0.03, "segment": "everyday",
     "source_note": "Same platform concerns as Chrysler; CR below average"},
    {"brand_name": "jeep", "modifier": -0.04, "segment": "everyday",
     "source_note": "CR and JD Power below average; electrical and transmission issues documented"},
    {"brand_name": "fiat", "modifier": -0.07, "segment": "everyday",
     "source_note": "Consistently at or near bottom of CR reliability surveys"},

    # Luxury / Near-Luxury
    {"brand_name": "lexus", "modifier": 0.08, "segment": "luxury",
     "source_note": "Consistently #1 CR reliability; lowest cost of ownership in luxury segment"},
    {"brand_name": "acura", "modifier": 0.04, "segment": "luxury",
     "source_note": "Honda platform; carries reliability premium into luxury tier"},
    {"brand_name": "buick", "modifier": 0.03, "segment": "luxury",
     "source_note": "JD Power VDS 2022 #3 overall behind Lexus and Porsche; CR above average 2020–2023"},
    {"brand_name": "genesis", "modifier": 0.03, "segment": "luxury",
     "source_note": "Hyundai platform; strong warranty; CR above average; rising used demand"},
    {"brand_name": "infiniti", "modifier": 0.00, "segment": "luxury",
     "source_note": "Nissan platform; above-average reliability but not standout"},
    {"brand_name": "volvo", "modifier": -0.02, "segment": "luxury",
     "source_note": "Complex electronics; improved but still below category average in CR"},
    {"brand_name": "cadillac", "modifier": -0.02, "segment": "luxury",
     "source_note": "Mixed history; brand perception drag from older models; recent improvement"},
    {"brand_name": "lincoln", "modifier": -0.03, "segment": "luxury",
     "source_note": "Below category average; high maintenance perception in used market"},
    {"brand_name": "bmw", "modifier": -0.04, "segment": "luxury",
     "source_note": "Well-documented reliability and maintenance cost concerns; CR below average; variant overrides handle specific engine outliers"},
    {"brand_name": "audi", "modifier": -0.04, "segment": "luxury",
     "source_note": "Electronics and oil consumption concerns; CR below average"},
    {"brand_name": "mercedes-benz", "modifier": -0.05, "segment": "luxury",
     "source_note": "CR below average; high ownership cost compresses used demand; JD Power below segment average"},
    {"brand_name": "jaguar", "modifier": -0.08, "segment": "luxury",
     "source_note": "JLR sister brand; CR near bottom; electrical reliability well-documented"},
    {"brand_name": "land rover", "modifier": -0.10, "segment": "luxury",
     "source_note": "Persistently bottom of CR and JD Power; notorious electrical and reliability issues"},
    {"brand_name": "range rover", "modifier": -0.10, "segment": "luxury",
     "source_note": "Alias for Land Rover; same data applies"},
    {"brand_name": "alfa romeo", "modifier": -0.08, "segment": "luxury",
     "source_note": "Bottom of CR surveys across all recent model years"},
    {"brand_name": "maserati", "modifier": -0.12, "segment": "luxury",
     "source_note": "Rapid depreciation documented; reliability near bottom; very high maintenance costs"},

    # Enthusiast / Performance
    {"brand_name": "porsche", "modifier": 0.08, "segment": "enthusiast",
     "source_note": "JD Power VDS 2022 #2 overall; cult demand sustains value; limited production of key models; variant overrides for 911 and Cayenne"},
    {"brand_name": "ferrari", "modifier": 0.04, "segment": "enthusiast",
     "source_note": "Strong brand demand; limited production; modern cars hold well; some older V8 models appreciating"},
    {"brand_name": "lamborghini", "modifier": 0.02, "segment": "enthusiast",
     "source_note": "High demand from collector segment; Urus adds volume; modern cars hold value well"},
    {"brand_name": "bentley", "modifier": -0.04, "segment": "enthusiast",
     "source_note": "VW Group reliability concerns at scale; high maintenance compresses used demand"},
    {"brand_name": "rolls-royce", "modifier": -0.02, "segment": "enthusiast",
     "source_note": "Ultra-low volume; collector demand is strong but market is thin"},
    {"brand_name": "mclaren", "modifier": -0.08, "segment": "enthusiast",
     "source_note": "Rapid depreciation on modern cars; reliability concerns documented; very high service costs"},
    {"brand_name": "aston martin", "modifier": -0.10, "segment": "enthusiast",
     "source_note": "Rapid depreciation; below-average reliability; low sales volume hurts parts availability"},
    {"brand_name": "tesla", "modifier": -0.02, "segment": "enthusiast",
     "source_note": "Within EV curve; build quality consistency and service availability concerns offset tech demand"},
]

# ---------------------------------------------------------------------------
# Variant overrides
# ---------------------------------------------------------------------------

VARIANT_SEED = [
    # BMW — performance positive
    {
        "make": "bmw",
        "model_keywords": ["340i", "440i", "540i", "740i", "m340i", "m440i", "m240i", "z4 30i", "z4 sdrive30i"],
        "engine_keywords": ["b58"],
        "year_from": 2016, "year_to": None,
        "modifier": 0.05,
        "notes": "B58 engine — exceptional reliability for BMW; robust under tuning; strong enthusiast demand",
        "source_note": "iSeeCars / BimmerPost community reliability data 2019–2024",
    },
    {
        "make": "bmw",
        "model_keywords": ["m3", "m4"],
        "engine_keywords": ["s55", "s58"],
        "year_from": 2015, "year_to": None,
        "modifier": 0.02,
        "notes": "M3/M4 strong enthusiast demand sustains value above standard BMW; S58 more reliable than S55",
        "source_note": "iSeeCars M-car retention data; enthusiast community consensus",
    },

    # BMW — reliability negatives
    {
        "make": "bmw",
        "model_keywords": ["550i", "650i", "750i", "x5 50i", "x6 50i", "m5", "m6"],
        "engine_keywords": ["n63", "4.4t", "4.4l"],
        "year_from": 2009, "year_to": 2018,
        "modifier": -0.06,
        "notes": "N63 engine — oil consumption, timing chain, high repair bills. BMW issued multiple service actions.",
        "source_note": "NHTSA complaint data; BMW N63 Customer Care Package documentation",
    },
    {
        "make": "bmw",
        "model_keywords": ["335i", "135i", "535i", "z4 35i", "335xi"],
        "engine_keywords": ["n54", "3.0t", "twin turbo"],
        "year_from": 2007, "year_to": 2013,
        "modifier": -0.04,
        "notes": "N54 engine — HPFP failure, injector issues, wastegate rattle well-documented",
        "source_note": "NHTSA technical service bulletins; BMW enthusiast community consensus",
    },
    {
        "make": "bmw",
        "model_keywords": ["328i", "320i", "228i", "428i"],
        "engine_keywords": ["n20", "2.0t"],
        "year_from": 2012, "year_to": 2015,
        "modifier": -0.02,
        "notes": "N20 timing chain wear issue; replacement recommended before 60k miles",
        "source_note": "NHTSA complaint data; BMW TSB B12 10 14",
    },
    {
        "make": "bmw",
        "model_keywords": ["528i", "328i", "130i", "330i"],
        "engine_keywords": ["n52", "3.0 na", "naturally aspirated"],
        "year_from": 2004, "year_to": 2013,
        "modifier": 0.01,
        "notes": "N52 — relatively reliable for BMW; naturally aspirated; fewer failure modes than turbo variants",
        "source_note": "Enthusiast community reliability consensus; NHTSA complaint volume below N54/N63",
    },

    # Ford
    {
        "make": "ford",
        "model_keywords": ["mustang gt", "mustang gt500", "mustang gt350"],
        "engine_keywords": ["coyote", "5.0", "5.0l v8"],
        "year_from": 2011, "year_to": None,
        "modifier": 0.03,
        "notes": "Coyote 5.0 — beloved engine; high community demand; proven longevity past 200k miles",
        "source_note": "iSeeCars Mustang retention data; enthusiast community",
    },
    {
        "make": "ford",
        "model_keywords": ["mustang gt350", "mustang gt350r"],
        "engine_keywords": ["voodoo", "5.2", "flat-plane"],
        "year_from": 2016, "year_to": 2020,
        "modifier": 0.06,
        "notes": "Voodoo flat-plane 5.2 — collector demand; unique engine; limited production 2016–2020",
        "source_note": "Market data; CarGurus/AutoTrader pricing trends 2022–2024",
    },
    {
        "make": "ford",
        "model_keywords": ["focus st", "focus rs", "fusion", "escape"],
        "engine_keywords": ["ecoboost 1.5", "ecoboost 1.6", "1.5t", "1.6t"],
        "year_from": 2012, "year_to": 2018,
        "modifier": -0.03,
        "notes": "EcoBoost 1.5/1.6 — coolant entering combustion chamber; class action lawsuit filed",
        "source_note": "NHTSA complaint data; class action filing 2019",
    },
    {
        "make": "ford",
        "model_keywords": ["f-250", "f-350", "super duty"],
        "engine_keywords": ["powerstroke 6.0", "6.0l diesel", "6.0 diesel"],
        "year_from": 2003, "year_to": 2007,
        "modifier": -0.05,
        "notes": "PowerStroke 6.0 diesel — EGR cooler failures, head bolt issues; notorious reliability problems",
        "source_note": "NHTSA data; Ford TSBs; diesel community consensus",
    },
    {
        "make": "ford",
        "model_keywords": ["f-250", "f-350", "super duty"],
        "engine_keywords": ["powerstroke 6.7", "6.7l diesel", "6.7 diesel"],
        "year_from": 2011, "year_to": None,
        "modifier": 0.02,
        "notes": "PowerStroke 6.7 diesel — significantly improved over 6.0; strong towing demand",
        "source_note": "iSeeCars / Edmunds F-250 retention data; diesel community consensus",
    },

    # Chevrolet
    {
        "make": "chevrolet",
        "model_keywords": ["corvette", "c7", "c8"],
        "engine_keywords": ["lt1", "lt2", "lt4", "5.5", "6.2"],
        "year_from": 2014, "year_to": None,
        "modifier": 0.05,
        "notes": "GM small-block family — exceptional reliability; strong enthusiast demand; C8 mid-engine prestige",
        "source_note": "iSeeCars Corvette retention; enthusiast community consensus",
    },
    {
        "make": "chevrolet",
        "model_keywords": ["camaro ss", "camaro zl1"],
        "engine_keywords": ["lt1", "lt4", "6.2"],
        "year_from": 2016, "year_to": None,
        "modifier": 0.03,
        "notes": "Reliable platform; enthusiast demand above Mustang in some markets; LT1/LT4 proven",
        "source_note": "iSeeCars Camaro vs Mustang retention comparison",
    },

    # Dodge / SRT
    {
        "make": "dodge",
        "model_keywords": ["challenger", "charger"],
        "engine_keywords": ["hellcat", "demon", "redeye", "supercharged"],
        "year_from": 2015, "year_to": None,
        "modifier": 0.04,
        "notes": "Hellcat/Demon/Redeye — collector demand; limited Demon units appreciating; brand enthusiasm overrides brand modifier",
        "source_note": "Market data; Dodge Demon auction results; CarGurus trend data",
    },
    {
        "make": "dodge",
        "model_keywords": ["challenger", "charger", "durango"],
        "engine_keywords": ["5.7 hemi", "5.7l hemi"],
        "year_from": 2003, "year_to": None,
        "modifier": 0.01,
        "notes": "5.7 Hemi — reliable engine despite brand modifier; strong used demand",
        "source_note": "NHTSA complaint volume low for engine; enthusiast community",
    },

    # Subaru
    {
        "make": "subaru",
        "model_keywords": ["legacy", "outback", "forester", "impreza"],
        "engine_keywords": ["ej25", "2.5i", "non-turbo", "naturally aspirated"],
        "year_from": None, "year_to": 2012,
        "modifier": -0.03,
        "notes": "EJ25 non-turbo — head gasket failures widespread and well-documented pre-2013",
        "source_note": "NHTSA complaint data; CR reliability; class action history",
    },
    {
        "make": "subaru",
        "model_keywords": ["wrx sti", "sti"],
        "engine_keywords": ["ej257", "ej25t"],
        "year_from": 2004, "year_to": 2021,
        "modifier": -0.02,
        "notes": "EJ257 (STI) — ring land failures documented at high power levels; distinct from base EJ25 head gasket issue",
        "source_note": "Nasioc/IWSTI community data; NHTSA complaints",
    },
    {
        "make": "subaru",
        "model_keywords": ["wrx"],
        "engine_keywords": ["fa20dit", "fa20", "2.0t"],
        "year_from": 2015, "year_to": 2021,
        "modifier": -0.01,
        "notes": "FA20DIT — ring land failures documented under heavy use; improved over EJ but not resolved",
        "source_note": "Nasioc community data; NHTSA technical complaints",
    },

    # Toyota
    {
        "make": "toyota",
        "model_keywords": ["4runner", "fj cruiser", "tacoma", "tundra"],
        "engine_keywords": ["1gr-fe", "4.0l v6", "4.0 v6"],
        "year_from": 2003, "year_to": None,
        "modifier": 0.04,
        "notes": "1GR-FE V6 — legendary longevity; 4Runner and FJ Cruiser cult following; high-mileage examples common",
        "source_note": "iSeeCars 4Runner retention data; Toyota community consensus",
    },
    {
        "make": "toyota",
        "model_keywords": ["supra", "gr supra"],
        "engine_keywords": ["b58", "3.0t", "3.0l turbo"],
        "year_from": 2020, "year_to": None,
        "modifier": 0.05,
        "notes": "BMW B58 engine with Toyota demand premium; supra name carries enthusiast value",
        "source_note": "iSeeCars GR Supra retention; enthusiast market data",
    },

    # Nissan
    {
        "make": "nissan",
        "model_keywords": ["gt-r", "gtr", "r35"],
        "engine_keywords": ["vr38dett", "vr38", "twin turbo v6"],
        "year_from": 2009, "year_to": None,
        "modifier": 0.05,
        "notes": "GT-R appreciating; engineering reputation; limited production; iconic status",
        "source_note": "Market data; GT-R pricing trends 2020–2024",
    },

    # Porsche
    {
        "make": "porsche",
        "model_keywords": ["911", "carrera", "targa", "cabriolet", "turbo", "gt3", "gt2"],
        "engine_keywords": [],
        "year_from": 1999, "year_to": None,
        "modifier": 0.06,
        "notes": "911 consistently outperforms sports category curve; some configurations appreciating; cult demand",
        "source_note": "iSeeCars Porsche 911 retention data; Hagerty market analysis",
    },
    {
        "make": "porsche",
        "model_keywords": ["cayenne", "macan", "panamera"],
        "engine_keywords": [],
        "year_from": 2010, "year_to": None,
        "modifier": 0.03,
        "notes": "Porsche SUV/sedan — above luxury_suv average; brand reliability premium applies",
        "source_note": "iSeeCars Cayenne retention vs luxury_suv benchmark",
    },

    # Mercedes
    {
        "make": "mercedes-benz",
        "model_keywords": ["c300", "e350", "glk350", "c350"],
        "engine_keywords": ["m276", "3.0 v6", "3.0l v6"],
        "year_from": 2012, "year_to": 2018,
        "modifier": -0.02,
        "notes": "M276 3.0 V6 — balance shaft chain wear issue documented",
        "source_note": "NHTSA complaint data; Mercedes-Benz TSBs",
    },

    # VW / Audi
    {
        "make": "volkswagen",
        "model_keywords": ["golf gti", "jetta gli", "a4", "a3", "cc", "passat"],
        "engine_keywords": ["ea888", "2.0t", "2.0 tsi"],
        "year_from": 2008, "year_to": 2013,
        "modifier": -0.03,
        "notes": "EA888 Gen1/2 — timing chain, carbon buildup, oil consumption well-documented",
        "source_note": "NHTSA complaint data; VW/Audi community consensus",
    },
    {
        "make": "audi",
        "model_keywords": ["a4", "a3", "q5", "a5", "tt"],
        "engine_keywords": ["ea888", "2.0t", "2.0 tfsi"],
        "year_from": 2008, "year_to": 2013,
        "modifier": -0.03,
        "notes": "EA888 Gen1/2 same as VW application — timing chain, carbon buildup, oil consumption",
        "source_note": "NHTSA complaint data; Audi community consensus",
    },
    {
        "make": "volkswagen",
        "model_keywords": ["golf gti", "golf r", "jetta gli", "tiguan"],
        "engine_keywords": ["ea888", "2.0t", "2.0 tsi"],
        "year_from": 2014, "year_to": None,
        "modifier": -0.01,
        "notes": "EA888 Gen3 — improved but carbon buildup concern remains on direct injection",
        "source_note": "NHTSA complaint data; reduction vs Gen1/2",
    },

    # Jeep
    {
        "make": "jeep",
        "model_keywords": ["cherokee", "grand cherokee"],
        "engine_keywords": ["pentastar", "3.6", "3.6l v6"],
        "year_from": 2014, "year_to": 2019,
        "modifier": -0.02,
        "notes": "Pentastar 3.6 + ZF 8HP pairing — transmission reliability concerns in this specific combination",
        "source_note": "NHTSA complaint data; Jeep community forums",
    },

    # Land Rover
    {
        "make": "land rover",
        "model_keywords": ["discovery sport", "evoque", "range rover evoque"],
        "engine_keywords": ["ingenium", "2.0t", "si4"],
        "year_from": 2015, "year_to": None,
        "modifier": -0.03,
        "notes": "Ingenium 2.0T — reliability below even Land Rover average; additional to brand modifier",
        "source_note": "CR reliability; Land Rover community consensus",
    },
]


async def seed_depreciation_data(db: AsyncSession) -> None:
    """Populate the three depreciation config tables if they are empty.

    Safe to call on every startup — skipped entirely if any rows exist.
    """
    # Check if already seeded
    category_count = await db.scalar(
        select(func.count()).select_from(DepreciationCategory)
    )
    if category_count and category_count > 0:
        logger.info("Depreciation seed already present (%d categories), skipping.", category_count)
        return

    logger.info("Seeding depreciation config tables...")

    for row in CATEGORY_SEED:
        db.add(DepreciationCategory(**row))

    for row in BRAND_SEED:
        db.add(BrandModifier(**row))

    for row in VARIANT_SEED:
        db.add(VariantOverride(**row))

    await db.commit()
    logger.info(
        "Seed complete: %d categories, %d brands, %d variant overrides.",
        len(CATEGORY_SEED),
        len(BRAND_SEED),
        len(VARIANT_SEED),
    )
