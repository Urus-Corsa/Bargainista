from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    Phase 2 will add AnalysisRun, AgentResult, and FinalReport tables here.
    """
    pass
