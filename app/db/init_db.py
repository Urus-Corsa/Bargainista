from app.db.seed import seed_depreciation_data
from app.db.session import AsyncSessionLocal, engine
from app.models.db_models import Base


async def init_db() -> None:
    """Create all tables that do not yet exist, then seed config data if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        await seed_depreciation_data(db)
