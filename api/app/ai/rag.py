"""Retrieval-Augmented Generation peste baza de cunoștințe locală.

Pipeline:  JSON (knowledge/) -> chunk-uri (o secțiune = un chunk) -> embeddings Ollama -> PostgreSQL
Căutare:   (1) filtru exact pe medicament, dacă știm medicamentul
           (2) similaritate cosinus: în SQL cu pgvector sau în Python (VECTOR_BACKEND=python)
           (3) fallback lexical dacă modelul de embedding nu e disponibil
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import USE_PGVECTOR, KnowledgeChunk
from .ollama_client import LLMClient, LLMError
from .text import normalize, tokens

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
SECTION_LABELS = {"ce_este": "Ce este", "administrare": "Administrare", "atentionari": "Atenționări",
                  "interactiuni": "Interacțiuni"}


@dataclass
class Drug:
    key: str
    name: str
    aliases: list[str]
    classes: list[str]
    sections: dict[str, str]


@lru_cache
def drug_catalog() -> dict[str, Drug]:
    data = json.loads((KNOWLEDGE_DIR / "medicamente.json").read_text(encoding="utf-8"))
    return {d["key"]: Drug(d["key"], d["name"], d["aliases"], d["classes"], d["sections"]) for d in data["drugs"]}


def match_drug(name: str) -> Drug | None:
    """'Amoxicilină 500 mg caps.' -> Drug(amoxicilina). Potrivire pe nume/denumiri comerciale."""
    toks = set(tokens(name))
    for d in drug_catalog().values():
        if toks & {normalize(a) for a in d.aliases}:
            return d
    norm = normalize(name)
    for d in drug_catalog().values():
        if any(normalize(a) in norm for a in d.aliases if len(a) > 4):
            return d
    return None


def _all_chunks() -> list[dict]:
    chunks = []
    for d in drug_catalog().values():
        for sec, text in d.sections.items():
            chunks.append({"source": "knowledge/medicamente.json", "title": f"{d.name} - {SECTION_LABELS.get(sec, sec)}",
                           "drug": d.key, "section": sec, "content": text})
    general = json.loads((KNOWLEDGE_DIR / "sfaturi_generale.json").read_text(encoding="utf-8"))
    for t in general["topics"]:
        chunks.append({"source": "knowledge/sfaturi_generale.json", "title": t["title"], "drug": None,
                       "section": "general", "content": t["content"]})
    return chunks


def ingest(db: Session, llm: LLMClient | None, batch: int = 16) -> dict:
    """(Re)construiește indexul. Rulat de `python -m app.seed`."""
    chunks = _all_chunks()
    embedded = 0
    vectors: list[list[float] | None] = [None] * len(chunks)
    if llm is not None:
        try:
            for i in range(0, len(chunks), batch):
                part = chunks[i: i + batch]
                embs = llm.embed([f"{c['title']}\n{c['content']}" for c in part])
                vectors[i: i + len(part)] = embs
                embedded += len(part)
        except LLMError as e:
            print(f"[rag] embeddings indisponibile, folosesc doar căutare lexicală: {e}")
    db.execute(delete(KnowledgeChunk))
    db.add_all([KnowledgeChunk(**c, embedding=v) for c, v in zip(chunks, vectors)])
    db.commit()
    return {"chunks": len(chunks), "embedded": embedded}


@dataclass
class Hit:
    title: str
    content: str
    source: str
    drug: str | None
    score: float

    def as_source(self) -> dict:
        return {"title": self.title, "source": self.source}


def _lexical(db: Session, query: str, k: int) -> list[Hit]:
    q = {t for t in tokens(query) if len(t) > 3}
    scored = []
    for c in db.scalars(select(KnowledgeChunk)):
        ct = set(tokens(c.title + " " + c.content))
        overlap = sum(1 for t in q if t in ct or any(x.startswith(t[:5]) for x in ct))
        if overlap:
            scored.append(Hit(c.title, c.content, c.source, c.drug, overlap / (len(q) or 1)))
    return sorted(scored, key=lambda h: -h.score)[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _vector_search(db: Session, qv: list[float], k: int) -> list[Hit]:
    if USE_PGVECTOR:
        dist = KnowledgeChunk.embedding.cosine_distance(qv)
        rows = db.execute(
            select(KnowledgeChunk, dist.label("d")).where(KnowledgeChunk.embedding.is_not(None)).order_by(dist).limit(k)
        ).all()
        return [Hit(c.title, c.content, c.source, c.drug, 1 - float(d)) for c, d in rows]
    # Varianta fără extensii: pentru câteva sute de fragmente, o căutare exhaustivă în Python e suficient de rapidă.
    scored = [(c, _cosine(qv, c.embedding)) for c in db.scalars(select(KnowledgeChunk)) if c.embedding]
    scored.sort(key=lambda x: -x[1])
    return [Hit(c.title, c.content, c.source, c.drug, s) for c, s in scored[:k]]


def search(db: Session, query: str, llm: LLMClient | None, k: int = 4, min_score: float | None = None) -> list[Hit]:
    if llm is not None:
        try:
            [qv] = llm.embed([query])
            hits = _vector_search(db, qv, k)
            if hits:
                return [h for h in hits if min_score is None or h.score >= min_score]
        except LLMError:
            pass
    return _lexical(db, query, k)


def drug_chunks(db: Session, drug_key: str) -> list[Hit]:
    rows = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.drug == drug_key).order_by(KnowledgeChunk.id))
    hits = [Hit(c.title, c.content, c.source, c.drug, 1.0) for c in rows]
    if not hits:  # index negenerat: citim direct din catalog
        d = drug_catalog()[drug_key]
        hits = [Hit(f"{d.name} - {SECTION_LABELS.get(s, s)}", t, "knowledge/medicamente.json", d.key, 1.0)
                for s, t in d.sections.items()]
    return hits


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(f"[{i + 1}] {h.title}\n{h.content}" for i, h in enumerate(hits))
