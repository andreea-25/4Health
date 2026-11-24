"""Ce au în comun cei doi agenți (pacient și medic): sesiuni de chat, istoric, căutare RAG, salvare.

Ce diferă între agenți (prompt, date accesibile, unelte, schema răspunsului) e în patient.py și doctor.py.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import ChatMessage, ChatSession, User
from .. import rag
from ..ollama_client import LLMClient
from ..rag import Hit


def get_session(db: Session, user: User | None, session_id: int | None, first_msg: str) -> ChatSession | None:
    """Conversația continuă (dacă e a utilizatorului) sau una nouă. Vizitatorii nu au sesiuni salvate."""
    if user is None:
        return None
    if session_id:
        s = db.get(ChatSession, session_id)
        if s and s.user_id == user.id:
            return s
    s = ChatSession(user_id=user.id, title=first_msg[:60])
    db.add(s)
    db.flush()
    return s


def history(session: ChatSession | None, visitor_history: list[dict] | None = None, n: int = 6) -> list[dict]:
    if session:
        return [{"role": m.role, "content": m.content} for m in session.messages[-n:]]
    return (visitor_history or [])[-n:]


def retrieve(db: Session, message: str, llm: LLMClient) -> list[Hit]:
    """Fragmente relevante din baza de cunoștințe. Dacă mesajul numește un medicament, fișa lui are prioritate."""
    s = get_settings()
    hits = rag.search(db, message, llm, k=s.rag_top_k, min_score=s.rag_min_score)
    drug = rag.match_drug(message)
    if drug:
        hits = rag.drug_chunks(db, drug.key)[:3] + [h for h in hits if h.drug != drug.key][:2]
    return hits


def save_exchange(db: Session, session: ChatSession | None, message: str, out: dict, meta_keys: tuple[str, ...]) -> dict:
    if session:
        db.add(ChatMessage(session_id=session.id, role="user", content=message))
        db.add(ChatMessage(session_id=session.id, role="assistant", content=out["reply"],
                           meta={k: out.get(k) for k in meta_keys}))
        db.commit()
        out["session_id"] = session.id
    else:
        out["session_id"] = None
    return out
