"""Asistentul conversațional „Iris”: sfaturi generale, întrebări despre rețete, orientare către medic."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import SPECIALTIES, ChatMessage, ChatSession, Prescription, Role, User
from . import rag
from .ollama_client import LLMClient
from .safety import DISCLAIMER, check_red_flags
from .triage import doctors_for

INTENTS = ["simptome", "medicamente", "reteta", "sfat_general", "altceva"]

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "intent": {"type": "string", "enum": INTENTS},
        "suggested_specialty": {"type": "string", "enum": list(SPECIALTIES.keys()) + ["niciuna"]},
    },
    "required": ["reply", "intent", "suggested_specialty"],
}

BASE_PROMPT = """Ești Iris, asistentul AI al platformei medicale 4Health din România. Răspunzi în limba română.
Rolul tău: explici rețete și medicamente, dai sfaturi generale de sănătate și recomanzi ce specialist să consulte utilizatorul.
Reguli:
- Nu pui diagnostice și nu prescrii/modifici medicamente sau doze. Pentru decizii medicale trimiți la medic.
- Te bazezi pe CONTEXT când e relevant; dacă nu știi, spui sincer și recomanzi medicul sau farmacistul.
- Răspunsuri scurte și clare (maxim 6-8 propoziții), cu pași concreți când ajută.
- "intent": simptome (descrie simptome), medicamente, reteta (despre rețetele lui), sfat_general, altceva.
- "suggested_specialty": specializarea potrivită DOAR dacă utilizatorul descrie simptome, altfel "niciuna".
- Dacă bănuiești o urgență, spune clar să sune la 112."""

DOCTOR_EXTRA = "\nVorbești cu un MEDIC: poți folosi terminologie medicală și poți fi mai tehnic."


def _patient_context(db: Session, user: User) -> str:
    p = user.patient
    if not p:
        return ""
    rxs = db.scalars(select(Prescription).where(Prescription.patient_id == p.id)
                     .order_by(Prescription.issued_at.desc()).limit(5)).all()
    lines = [f"Pacient: {user.first_name}. Alergii: {p.allergies or 'nedeclarate'}. "
             f"Afecțiuni cronice: {p.chronic_conditions or 'nedeclarate'}."]
    for r in rxs:
        meds = "; ".join(f"{i.drug_name} {i.concentration_form} - {i.administration} {i.duration}".strip() for i in r.items)
        lines.append(f"Rețetă {r.issued_at.isoformat()} (diagnostic: {r.diagnosis or '-'}): {meds}")
    return "\n".join(lines)


def _get_session(db: Session, user: User | None, session_id: int | None, first_msg: str) -> ChatSession | None:
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


def respond(db: Session, user: User | None, message: str, session_id: int | None, llm: LLMClient,
            visitor_history: list[dict] | None = None) -> dict:
    session = _get_session(db, user, session_id, message)
    safety = check_red_flags(message)

    if safety.urgent:
        out = {"reply": safety.message, "intent": "urgenta", "urgent": True, "red_flags": safety.flags,
               "specialty": None, "doctors": [], "sources": [], "model": "safety_rules"}
    else:
        s = get_settings()
        hits = rag.search(db, message, llm, k=s.rag_top_k, min_score=s.rag_min_score)
        drug = rag.match_drug(message)
        if drug:
            hits = rag.drug_chunks(db, drug.key)[:3] + [h for h in hits if h.drug != drug.key][:2]

        system = BASE_PROMPT + (DOCTOR_EXTRA if user and user.role == Role.doctor else "")
        ctx = "CONTEXT (baza de cunoștințe):\n" + (rag.format_context(hits) or "(gol)")
        if user and user.role == Role.patient:
            ctx += "\n\nDATELE PACIENTULUI (folosește-le doar dacă sunt relevante):\n" + _patient_context(db, user)

        history = [{"role": m.role, "content": m.content} for m in (session.messages[-6:] if session else [])]
        if not session and visitor_history:
            history = visitor_history[-6:]
        msgs = [{"role": "system", "content": system + "\n\n" + ctx}, *history, {"role": "user", "content": message}]
        res = llm.chat_json(msgs, CHAT_SCHEMA)
        data = res.data if isinstance(res.data, dict) else {}
        spec = data.get("suggested_specialty")
        intent = data.get("intent", "altceva")
        doctors = []
        if intent == "simptome" and spec in SPECIALTIES:
            doctors = doctors_for(db, spec, user.patient if user else None, limit=3)
        else:
            spec = None
        out = {"reply": data.get("reply", "").strip() or "Nu am putut formula un răspuns. Încearcă să reformulezi.",
               "intent": intent, "urgent": False, "red_flags": [],
               "specialty": {"key": spec, "label": SPECIALTIES[spec]} if spec else None,
               "doctors": doctors, "sources": [h.as_source() for h in hits], "model": res.model}

    out["disclaimer"] = DISCLAIMER
    if session:
        db.add(ChatMessage(session_id=session.id, role="user", content=message))
        db.add(ChatMessage(session_id=session.id, role="assistant", content=out["reply"],
                           meta={k: out[k] for k in ("intent", "specialty", "sources", "model", "urgent")}))
        db.commit()
        out["session_id"] = session.id
    else:
        out["session_id"] = None
    return out
