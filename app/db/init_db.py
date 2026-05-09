from app.db.session import engine
from app.models.db_models import Base


async def init_db() -> None:
    """Create all tables that do not yet exist. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
