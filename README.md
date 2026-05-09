# Vehicle Analysis Platform

Multi-agent AI system that analyzes used car listings and produces a structured buy / negotiate / pass recommendation.

Three specialized agents run in parallel — Vision (damage assessment from photos), History (red flag extraction from listing text), Finance (depreciation curves and cost analysis) — coordinated by a LangGraph orchestrator and delivered via a real-time WebSocket API.

**Stack:** FastAPI · LangGraph · Celery · Redis · PostgreSQL · Docker · AWS EC2

> Work in progress. Full README with architecture diagram and setup instructions coming at project completion.
