"""Configurare centralizată, citită din variabile de mediu / fișierul .env."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]  # rădăcina repo-ului


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Bază de date (PostgreSQL) ---
    database_url: str = "postgresql+psycopg://health4:health4@localhost:5432/health4"

    # --- Autentificare ---
    jwt_secret: str = "dev-only-secret-schimba-ma-in-fisierul-env-0123456789"
    jwt_expire_minutes: int = 8 * 60
    # Conturile de doctor se creează doar cu acest cod (evită ca oricine să se declare medic)
    doctor_invite_code: str = "DOCTOR-2026"

    # --- Ollama ---
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:3b"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024
    llm_temperature: float = 0.2
    llm_num_ctx: int = 4096
    llm_timeout_s: float = 180.0

    # --- RAG ---
    # "python"   = embeddings salvate ca JSON, similaritatea calculată în Python (PostgreSQL simplu, fără extensii)
    # "pgvector" = embeddings în coloană vector(n), căutare în SQL (necesită extensia pgvector, ex. imaginea Docker)
    vector_backend: Literal["python", "pgvector"] = "python"
    rag_top_k: int = 4
    # Fragmentele sub acest scor de similaritate (cosinus) nu sunt trimise modelului / afișate ca surse.
    # Calibrează-l pe modelul tău de embedding cu `python -m eval.run_eval --task retrieval`.
    rag_min_score: float = 0.45

    # --- Frontend static ---
    web_dir: Path = ROOT_DIR / "web"


@lru_cache
def get_settings() -> Settings:
    return Settings()
