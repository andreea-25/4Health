from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import chat as chat_ai
from ..ai import dosage, triage
from ..ai.ollama_client import LLMClient, LLMError, get_llm
from ..ai.prescription import interpret
from ..db import get_db
from ..deps import get_current_user, get_optional_user
from ..models import AIInterpretation, ChatSession, Prescription, Role, User
from ..schemas import ChatIn, DosageIn, RecommendIn

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _llm_unavailable(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@router.get("/health")
def health(llm: LLMClient = Depends(get_llm)):
    return llm.health() if hasattr(llm, "health") else {"ollama": "custom-client"}


@router.post("/prescriptions/{rx_id}/interpret")
def interpret_prescription(
    rx_id: int,
    refresh: bool = Query(False, description="Regenerează, ignorând cache-ul"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    rx = db.get(Prescription, rx_id)
    allowed = rx and (
        (user.role == Role.patient and user.patient and rx.patient_id == user.patient.id)
        or (user.role == Role.doctor and user.doctor and rx.doctor_id == user.doctor.id)
    )
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rețetă inexistentă.")

    if not refresh:
        cached = db.scalar(
            select(AIInterpretation)
            .where(AIInterpretation.prescription_id == rx.id, AIInterpretation.model == llm.model)
            .order_by(AIInterpretation.id.desc())
        )
        if cached:
            return {**cached.output, "cached": True}
    try:
        out = interpret(db, rx, llm)
    except LLMError as e:
        raise _llm_unavailable(e)
    db.add(AIInterpretation(prescription_id=rx.id, model=out["model"], output=out, latency_ms=out["latency_ms"]))
    db.commit()
    return {**out, "cached": False}


class VisitorChatIn(ChatIn):
    history: list[dict] | None = None  # vizitatorii trimit istoricul din browser


@router.post("/chat")
def chat(
    data: VisitorChatIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    hist = [m for m in (data.history or []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    try:
        return chat_ai.respond(db, user, data.message, data.session_id, llm, visitor_history=hist)
    except LLMError as e:
        raise _llm_unavailable(e)


@router.get("/chat/sessions")
def list_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.id.desc()).limit(20))
    return [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in rows]


@router.get("/chat/sessions/{sid}")
def get_session(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversație inexistentă.")
    return {"id": s.id, "title": s.title,
            "messages": [{"role": m.role, "content": m.content, "meta": m.meta} for m in s.messages]}


@router.delete("/chat/sessions/{sid}", status_code=204)
def delete_session(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversație inexistentă.")
    db.delete(s)
    db.commit()


@router.post("/recommend")
def recommend(
    data: RecommendIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    return triage.recommend(db, data.symptoms, llm, user.patient if user else None)


class DosageOut(BaseModel):
    dosage: dict
    method: str
    schedule: list[str]


@router.post("/dosage", response_model=DosageOut)
def parse_dosage(data: DosageIn, llm: LLMClient = Depends(get_llm)):
    """Folosit în formularul medicului: previzualizează cum va înțelege sistemul indicația."""
    d, method = dosage.extract(data.text, llm)
    return DosageOut(dosage=d, method=method, schedule=dosage.build_schedule(d))
