"""AGENTUL MEDICULUI: sprijin clinic pentru decizie (clinical decision support). Decizia rămâne la medic.

Promptul:  prompts.DOCTOR_CHAT_PROMPT, prompts.DOCTOR_SUMMARY_PROMPT
Date:      doar pacienții ACESTUI medic (îl au ca medic de familie sau au primit rețete de la el)
Unelte (deterministe, fără LLM, deci verificabile și testabile):
  1. patient_facts()   - istoricul structurat al pacientului + tratamentele active
  2. check_safety()    - alergii și interacțiuni pentru un set de medicamente (folosit și la prescriere)
  3. summarize()       - rezumat clinic scris de LLM PE BAZA faptelor de la 1 și 2
Diferență față de agentul pacientului: medicul poate scrie „pacient cu durere toracică” fără ca mesajul
să fie tratat ca o urgență a utilizatorului, deci aici NU se aplică filtrul de red flags.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import DoctorProfile, PatientProfile, Prescription, User
from .. import prompts, rag
from ..dosage import extract_rules
from ..ollama_client import LLMClient, LLMError
from ..safety import allergy_alert
from ..text import normalize
from . import base

INTENTS = ["pacient", "medicamente", "interactiuni", "altceva"]

CHAT_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "intent": {"type": "string", "enum": INTENTS}},
    "required": ["reply", "intent"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "key_points": {"type": "array", "items": {"type": "string"}}},
    "required": ["summary", "key_points"],
}

DEFAULT_DURATION_DAYS = 30   # dacă durata nu poate fi citită din rețetă, considerăm tratamentul activ 30 de zile
SEVERITY_ORDER = {"majora": 0, "moderata": 1, "minora": 2}


# ------------------------------------------------------------------ acces
def can_access(db: Session, doctor: DoctorProfile, patient: PatientProfile) -> bool:
    """Confidențialitate: medicul vede doar pacienții lui."""
    if patient.family_doctor_id == doctor.id:
        return True
    return db.scalar(select(Prescription.id).where(Prescription.doctor_id == doctor.id,
                                                   Prescription.patient_id == patient.id).limit(1)) is not None


# ------------------------------------------------------------------ unealta 1: istoric + tratamente active
def _treatment_end(issued: date, duration_text: str, administration: str) -> date:
    days = extract_rules(" ".join(x for x in [duration_text, administration] if x)).get("duration_days")
    return issued + timedelta(days=days or DEFAULT_DURATION_DAYS)


def active_medications(patient: PatientProfile, today: date | None = None) -> list[dict]:
    today = today or date.today()
    meds = []
    for rx in patient.prescriptions:
        for it in rx.items:
            end = _treatment_end(rx.issued_at, it.duration, it.administration)
            if end >= today:
                meds.append({"drug_name": it.drug_name, "concentration_form": it.concentration_form,
                             "administration": it.administration, "issued_at": rx.issued_at.isoformat(),
                             "until": end.isoformat(), "prescribed_by": f"Dr. {rx.doctor.user.last_name}"})
    return meds


def patient_facts(patient: PatientProfile) -> dict:
    rxs = sorted(patient.prescriptions, key=lambda r: r.issued_at, reverse=True)
    return {
        "patient_id": patient.id,
        "name": patient.user.full_name,
        "allergies": patient.allergies or "",
        "chronic_conditions": patient.chronic_conditions or "",
        "active_medications": active_medications(patient),
        "prescription_history": [
            {"date": r.issued_at.isoformat(), "diagnosis": r.diagnosis, "doctor": f"Dr. {r.doctor.user.last_name}",
             "drugs": [i.drug_name for i in r.items]} for r in rxs[:10]
        ],
    }


# ------------------------------------------------------------------ unealta 2: alergii + interacțiuni
def _identity(drug_name: str) -> tuple[str, set[str]]:
    """Cum „se recunoaște” un medicament: numele scris (normalizat) + cheia, denumirile și clasele din baza de cunoștințe."""
    exact = set()
    d = rag.match_drug(drug_name)
    if d:
        exact |= {normalize(x) for x in [d.key, *d.aliases, *d.classes]}
    return normalize(drug_name), exact


def _matches(term: str, name: str, exact: set[str]) -> bool:
    """Potrivire exactă pe cheie/denumire/clasă sau cuvânt întreg în numele scris de medic.
    (Cuvânt întreg, nu subșir: „calciu” nu trebuie să prindă clasa „blocant al canalelor de calciu”.)"""
    t = normalize(term)
    return t in exact or re.search(rf"\b{re.escape(t)}\b", name) is not None


def _interaction(a: str, b: str) -> dict | None:
    """Caută o interacțiune între două medicamente, în ambele sensuri (A declară B sau B declară A)."""
    da, db_ = rag.match_drug(a), rag.match_drug(b)
    if da and db_ and da.key == db_.key:
        return None
    for src, other in ((da, b), (db_, a)):
        if not src:
            continue
        name, exact = _identity(other)
        for rule in src.interactions:
            if any(_matches(w, name, exact) for w in rule["with"]):
                return {"drug_a": a, "drug_b": b, "severity": rule["severity"], "note": rule["note"]}
    return None


def check_safety(patient: PatientProfile, new_drugs: list[str]) -> dict:
    """Verifică medicamentele noi față de alergiile pacientului, tratamentul activ și între ele.
    Fără `new_drugs` verifică doar tratamentul activ (folosit în rezumat)."""
    active = [m["drug_name"] for m in active_medications(patient)]
    new_drugs = [d.strip() for d in new_drugs if d and d.strip()]

    allergy_alerts = []
    for name in new_drugs or active:
        d = rag.match_drug(name)
        msg = allergy_alert(patient.allergies or "", d.key if d else None, d.classes if d else [], d.name if d else name,
                            audience="doctor")
        if msg:
            allergy_alerts.append({"drug": name, "message": msg})

    pairs = []
    if new_drugs:
        pairs += [(n, a) for n in new_drugs for a in active]
        pairs += [(new_drugs[i], new_drugs[j]) for i in range(len(new_drugs)) for j in range(i + 1, len(new_drugs))]
    else:
        pairs += [(active[i], active[j]) for i in range(len(active)) for j in range(i + 1, len(active))]

    seen, interactions = set(), []
    for a, b in pairs:
        key = frozenset((normalize(a), normalize(b)))
        if key in seen:
            continue
        seen.add(key)
        hit = _interaction(a, b)
        if hit:
            interactions.append(hit)
    interactions.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))

    unknown = sorted({n for n in (new_drugs or active) if not rag.match_drug(n)})
    return {"allergy_alerts": allergy_alerts, "interactions": interactions, "unknown_drugs": unknown,
            "active_medications": active,
            "note": "Verificare limitată la baza de cunoștințe 4Health; medicamentele din unknown_drugs nu au fost verificate."
            if unknown else ""}


def format_alerts(safety: dict) -> str:
    lines = [f"- ALERGIE: {a['message']}" for a in safety["allergy_alerts"]]
    lines += [f"- INTERACȚIUNE {i['severity'].upper()}: {i['drug_a']} + {i['drug_b']}: {i['note']}"
              for i in safety["interactions"]]
    return "\n".join(lines) or "(nicio alertă)"


def format_facts(f: dict) -> str:
    meds = "; ".join(f"{m['drug_name']} {m['concentration_form']} ({m['administration']}, până la {m['until']})"
                     for m in f["active_medications"]) or "niciunul"
    hist = "\n".join(f"  {h['date']} - {h['diagnosis'] or 'fără diagnostic'} ({h['doctor']}): {', '.join(h['drugs'])}"
                     for h in f["prescription_history"]) or "  (fără rețete)"
    return (f"Pacient: {f['name']}\nAlergii: {f['allergies'] or 'nedeclarate'}\n"
            f"Afecțiuni cronice: {f['chronic_conditions'] or 'nedeclarate'}\nTratament activ: {meds}\n"
            f"Istoric rețete:\n{hist}")


# ------------------------------------------------------------------ unealta 3: rezumat clinic
def summarize(patient: PatientProfile, llm: LLMClient | None) -> dict:
    facts = patient_facts(patient)
    safety = check_safety(patient, [])
    out = {"facts": facts, "alerts": safety, "summary": None, "key_points": [], "model": None}
    if llm is None:
        return out
    try:
        res = llm.chat_json([
            {"role": "system", "content": prompts.DOCTOR_SUMMARY_PROMPT},
            {"role": "user", "content": "DATELE PACIENTULUI:\n" + format_facts(facts) + "\n\nALERTE:\n" + format_alerts(safety)},
        ], SUMMARY_SCHEMA)
        data = res.data if isinstance(res.data, dict) else {}
        out.update(summary=data.get("summary"), key_points=[k for k in data.get("key_points") or [] if isinstance(k, str)][:5],
                   model=res.model)
    except LLMError as e:
        out["llm_error"] = str(e)   # faptele și alertele rămân utile și fără LLM
    return out


# ------------------------------------------------------------------ chatul medicului
def respond(db: Session, user: User, message: str, session_id: int | None, llm: LLMClient,
            patient: PatientProfile | None = None) -> dict:
    session = base.get_session(db, user, session_id, message)
    hits = base.retrieve(db, message, llm)
    ctx = "CONTEXT (baza de cunoștințe):\n" + (rag.format_context(hits) or "(gol)")
    if patient:
        safety = check_safety(patient, [])
        ctx += "\n\nDATELE PACIENTULUI:\n" + format_facts(patient_facts(patient)) + "\n\nALERTE:\n" + format_alerts(safety)
    else:
        ctx += "\n\nDATELE PACIENTULUI: (niciun pacient selectat)"

    msgs = [{"role": "system", "content": prompts.DOCTOR_CHAT_PROMPT + "\n\n" + ctx},
            *base.history(session), {"role": "user", "content": message}]
    res = llm.chat_json(msgs, CHAT_SCHEMA)
    data = res.data if isinstance(res.data, dict) else {}
    out = {"reply": (data.get("reply") or "").strip() or "Nu am putut formula un răspuns.",
           "intent": data.get("intent", "altceva"), "agent": "doctor",
           "patient_id": patient.id if patient else None,
           "sources": [h.as_source() for h in hits], "model": res.model, "urgent": False,
           "disclaimer": "Sprijin pentru decizie generat automat; verificați informațiile. Decizia clinică vă aparține."}
    return base.save_exchange(db, session, message, out, ("agent", "intent", "patient_id", "sources", "model"))
