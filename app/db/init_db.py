from app.db.seed import seed_depreciation_data
from app.db.session import AsyncSessionLocal


async def init_db() -> None:
    """Seed depreciation config data on first run.

    Tables are created by Alembic migrations (`alembic upgrade head`) before the
    application starts. This function only populates seed data — it never creates
    or alters tables.
    """
    async with AsyncSessionLocal() as db:
        await seed_depreciation_data(db)
