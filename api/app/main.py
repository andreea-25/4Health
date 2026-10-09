"""Punctul de intrare: `uvicorn app.main:app --reload` (din folderul api/)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .routers import ai, auth, doctors, patients


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="4Health API",
    description="Platformă medicală cu portal pentru pacienți și medici și asistent AI local (Ollama).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(doctors.public)
app.include_router(doctors.router)
app.include_router(patients.router)
app.include_router(ai.router)


@app.get("/api/health", tags=["public"])
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/web/index.html")


web_dir = get_settings().web_dir
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=web_dir, html=True), name="web")
