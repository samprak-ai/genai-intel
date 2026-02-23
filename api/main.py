"""
GenAI-Intel FastAPI Application
Serves the REST API for the dashboard and wraps the pipeline.

Run locally:
    uvicorn api.main:app --reload --port 8000

Docs available at:
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""

import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()  # Load .env before any imports that read os.getenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import startups, analytics, pipeline as pipeline_router
from api.scheduler import scheduler


# ---------------------------------------------------------------------------
# Lifespan — start/stop APScheduler with the server process
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print("[scheduler] APScheduler started — weekly pipeline runs every Monday 06:00 UTC")
    yield
    scheduler.shutdown(wait=False)
    print("[scheduler] APScheduler stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GenAI-Intel API",
    description="Track cloud and AI provider attribution for AI startups",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the Next.js dashboard
# FRONTEND_URL is set as an env var on Railway (e.g. "https://genai-intel.vercel.app")
_frontend_url = os.getenv("FRONTEND_URL", "")
_cors_origins = [
    "http://localhost:3000",       # local Next.js dev
    "https://*.vercel.app",        # Vercel preview deployments
]
if _frontend_url:
    _cors_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(startups.router)
app.include_router(analytics.router)
app.include_router(pipeline_router.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health():
    """Health check — used by Railway/Render uptime monitoring"""
    return {"status": "ok", "version": app.version}


@app.get("/", tags=["meta"])
def root():
    return {"message": "GenAI-Intel API", "docs": "/docs"}
