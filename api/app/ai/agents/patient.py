"""AGENTUL PACIENTULUI: explicații și sfaturi, fără diagnostic.

Promptul:  prompts.PATIENT_CHAT_PROMPT
Date:      doar ale pacientului logat (alergii, afecțiuni, ultimele rețete). Vizitatorii: fără date personale.
Siguranță: mesajele cu semne de urgență primesc răspuns fix cu 112 și NU ajung la LLM.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import SPECIALTIES, Prescription, User
from .. import prompts, rag
from ..ollama_client import LLMClient
from ..safety import DISCLAIMER, check_red_flags
from ..triage import doctors_for
from . import base

INTENTS = ["simptome", "medicamente", "reteta", "sfat_general", "altceva"]

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "intent": {"type": "string", "enum": INTENTS},
        "suggested_specialty": {"type": "string", "enum": list(SPECIALTIES.keys()) + ["niciuna"]},
    },
    "required": ["reply", "intent", "suggested_specialty"],
}


def patient_context(db: Session, user: User) -> str:
    """Datele pacientului puse în prompt. Modifică aici dacă vrei să-i dai agentului mai multe/mai puține date."""
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


def respond(db: Session, user: User | None, message: str, session_id: int | None, llm: LLMClient,
            visitor_history: list[dict] | None = None) -> dict:
    session = base.get_session(db, user, session_id, message)
    safety = check_red_flags(message)

    if safety.urgent:
        out = {"reply": safety.message, "intent": "urgenta", "urgent": True, "red_flags": safety.flags,
               "specialty": None, "doctors": [], "sources": [], "model": "safety_rules"}
    else:
        hits = base.retrieve(db, message, llm)
        ctx = "CONTEXT (baza de cunoștințe):\n" + (rag.format_context(hits) or "(gol)")
        if user and user.patient:
            ctx += "\n\nDATELE PACIENTULUI (folosește-le doar dacă sunt relevante):\n" + patient_context(db, user)

        msgs = [{"role": "system", "content": prompts.PATIENT_CHAT_PROMPT + "\n\n" + ctx},
                *base.history(session, visitor_history), {"role": "user", "content": message}]
        res = llm.chat_json(msgs, SCHEMA)
        data = res.data if isinstance(res.data, dict) else {}
        spec, intent = data.get("suggested_specialty"), data.get("intent", "altceva")
        doctors = []
        if intent == "simptome" and spec in SPECIALTIES:
            doctors = doctors_for(db, spec, user.patient if user else None, limit=3)
        else:
            spec = None
        out = {"reply": (data.get("reply") or "").strip() or "Nu am putut formula un răspuns. Încearcă să reformulezi.",
               "intent": intent, "urgent": False, "red_flags": [],
               "specialty": {"key": spec, "label": SPECIALTIES[spec]} if spec else None,
               "doctors": doctors, "sources": [h.as_source() for h in hits], "model": res.model}

    out["agent"] = "patient"
    out["disclaimer"] = DISCLAIMER
    return base.save_exchange(db, session, message, out, ("agent", "intent", "specialty", "sources", "model", "urgent"))
