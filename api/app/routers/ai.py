from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import dosage, triage
from ..ai.agents import doctor as doctor_agent
from ..ai.agents import patient as patient_agent
from ..ai.ollama_client import LLMClient, LLMError, get_llm
from ..ai.prescription import interpret
from ..db import get_db
from ..deps import get_current_user, get_optional_user, require_doctor
from ..models import AIInterpretation, ChatSession, DoctorProfile, PatientProfile, Prescription, Role, User
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


# ============================== AGENTUL PACIENTULUI ==============================
class PatientChatIn(ChatIn):
    history: list[dict] | None = None  # vizitatorii (nelogați) trimit istoricul din browser


@router.post("/patient/chat")
def patient_chat(
    data: PatientChatIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    """Pacienți și vizitatori. Medicii au propriul asistent (/api/ai/doctor/chat)."""
    if user and user.role != Role.patient:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Medicii folosesc asistentul clinic: /api/ai/doctor/chat")
    hist = [m for m in (data.history or []) if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    try:
        return patient_agent.respond(db, user, data.message, data.session_id, llm, visitor_history=hist)
    except LLMError as e:
        raise _llm_unavailable(e)


# ============================== AGENTUL MEDICULUI ==============================
def _my_patient(db: Session, doctor: DoctorProfile, patient_id: int) -> PatientProfile:
    patient = db.get(PatientProfile, patient_id)
    if not patient or not doctor_agent.can_access(db, doctor, patient):
        # 404 (nu 403) ca să nu dezvăluim dacă pacientul există
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pacientul nu există sau nu este pacientul dumneavoastră.")
    return patient


class DoctorChatIn(ChatIn):
    patient_id: int | None = None   # pacientul despre care se discută (opțional)


@router.post("/doctor/chat")
def doctor_chat(
    data: DoctorChatIn,
    doctor: DoctorProfile = Depends(require_doctor),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    patient = _my_patient(db, doctor, data.patient_id) if data.patient_id else None
    try:
        return doctor_agent.respond(db, doctor.user, data.message, data.session_id, llm, patient)
    except LLMError as e:
        raise _llm_unavailable(e)


@router.get("/doctor/patients/{patient_id}/summary")
def doctor_patient_summary(
    patient_id: int,
    use_llm: bool = Query(True, description="false = doar fapte și alerte, fără rezumatul scris de LLM"),
    doctor: DoctorProfile = Depends(require_doctor),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    """Unealta 1 + 3: istoricul pacientului, tratamentul activ, alerte și rezumat clinic."""
    return doctor_agent.summarize(_my_patient(db, doctor, patient_id), llm if use_llm else None)


class SafetyCheckIn(BaseModel):
    patient_id: int
    drugs: list[str] = Field(default_factory=list, max_length=20)


@router.post("/doctor/safety-check")
def doctor_safety_check(
    data: SafetyCheckIn,
    doctor: DoctorProfile = Depends(require_doctor),
    db: Session = Depends(get_db),
):
    """Unealta 2: alergii + interacțiuni pentru medicamentele pe care medicul vrea să le prescrie.
    Disponibilă pentru orice pacient căruia medicul îi poate emite rețetă (inclusiv găsit după CNP),
    pentru că verificarea e necesară exact în momentul prescrierii."""
    patient = db.get(PatientProfile, data.patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pacient inexistent.")
    return doctor_agent.check_safety(patient, data.drugs)


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
