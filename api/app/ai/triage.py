"""Recomandarea specializării / medicului pe baza simptomelor descrise.

 1. Red flags deterministe (safety.py) -> urgență, fără LLM.
 2. Clasificare în specializare: LLM cu ieșire constrânsă la enum (nu poate inventa specializări),
    cu fallback pe un clasificator baseline cu cuvinte-cheie.
 3. Medicii din DB cu specializarea respectivă (medicul de familie al pacientului primul, dacă e cazul).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SPECIALTIES, DoctorProfile, PatientProfile, User
from ..serializers import doctor_out
from .ollama_client import LLMClient, LLMError
from .safety import check_red_flags
from .text import normalize

KEYWORDS: dict[str, list[str]] = {
    "cardiologie": ["palpitat", "inima", "tensiune", "hipertens", "puls", "aritmi", "umflate glezne", "glezne umflate"],
    "dermatologie": ["piele", "eruptie", "cos", "acnee", "mancarim", "alunit", "psoriazis", "eczema", "pete", "par cade", "caderea parului", "unghi"],
    "endocrinologie": ["tiroid", "glicemi", "diabet", "zahar", "hormon", "slabesc fara", "ma ingras"],
    "gastroenterologie": ["stomac", "burta", "abdomen", "arsur", "reflux", "diaree", "constipat", "balon", "greata", "ficat", "digesti"],
    "ginecologie": ["menstrua", "ciclu", "sarcin", "gravid", "ovar", "uter", "vagin", "san ", "sani"],
    "neurologie": ["migren", "durere de cap", "dureri de cap", "ameteal", "amorteal", "furnicat", "tremur", "memori", "vertij"],
    "oftalmologie": ["ochi", "vedere", "vad incetosat", "vad neclar", "ochii", "lacrim", "conjunctiv"],
    "orl": ["gat", "ureche", "urechi", "nas infundat", "sinus", "amigdal", "raguseal", "auz", "tiuit"],
    "ortopedie": ["genunchi", "spate", "coloana", "articulat", "umar", "fractur", "entors", "glezna", "sold", "lombar"],
    "pediatrie": ["copilul", "copil", "bebelus", "fetita", "baietel", "sugar"],
    "pneumologie": ["tuse", "tusesc", "respir", "astm", "plamani", "bronsit", "sforai", "expector"],
    "psihiatrie": ["anxiet", "depres", "trist", "atac de panica", "panica", "insomni", "nu pot dormi", "stres", "neliniste"],
    "urologie": ["urin", "rinichi", "vezica", "prostat", "ustur", "pietre la rinichi", "colica renala"],
}

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "specialty": {"type": "string", "enum": list(SPECIALTIES.keys())},
        "reasoning": {"type": "string"},
    },
    "required": ["specialty", "reasoning"],
}

SYSTEM_PROMPT = (
    "Ești un asistent de triaj pentru o clinică din România. Pe baza simptomelor descrise, alegi specializarea "
    "medicală cea mai potrivită pentru o primă consultație. Dacă simptomele sunt vagi, generale sau implică mai multe "
    "sisteme, alege 'medicina_familie'. Dacă pacientul vorbește despre un copil, alege 'pediatrie'. "
    "'reasoning' = o propoziție scurtă în română, fără diagnostic. Specializări permise: "
    + ", ".join(f"{k} ({v})" for k, v in SPECIALTIES.items())
)


def classify_rules(text: str) -> str:
    t = " " + normalize(text) + " "
    scores = {spec: sum(1 for kw in kws if kw in t) for spec, kws in KEYWORDS.items()}
    if scores["pediatrie"]:
        return "pediatrie"
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "medicina_familie"


def classify_llm(text: str, llm: LLMClient, model: str | None = None) -> tuple[str, str]:
    res = llm.chat_json([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
                        TRIAGE_SCHEMA, model=model, temperature=0)
    spec = res.data.get("specialty") if isinstance(res.data, dict) else None
    if spec not in SPECIALTIES:
        raise LLMError(f"Specializare invalidă: {spec}")
    return spec, str(res.data.get("reasoning", ""))


def doctors_for(db: Session, specialty: str, patient: PatientProfile | None, limit: int = 5) -> list[dict]:
    q = select(DoctorProfile).join(User).where(DoctorProfile.specialty == specialty).order_by(User.last_name)
    docs = list(db.scalars(q))
    if patient and patient.family_doctor and specialty == "medicina_familie":
        docs = [patient.family_doctor] + [d for d in docs if d.id != patient.family_doctor_id]
    return [doctor_out(d).model_dump() for d in docs[:limit]]


def recommend(db: Session, text: str, llm: LLMClient | None, patient: PatientProfile | None = None) -> dict:
    safety = check_red_flags(text)
    if safety.urgent:
        return {"urgent": True, "red_flags": safety.flags, "message": safety.message,
                "specialty": None, "specialty_label": None, "reasoning": "", "method": "safety_rules", "doctors": []}
    method, reasoning = "rules", ""
    spec = classify_rules(text)
    if llm is not None:
        try:
            spec, reasoning = classify_llm(text, llm)
            method = "llm"
        except LLMError:
            pass
    doctors = doctors_for(db, spec, patient)
    if not doctors and spec != "medicina_familie":
        doctors = doctors_for(db, "medicina_familie", patient)
    return {
        "urgent": False, "red_flags": [], "message": None,
        "specialty": spec, "specialty_label": SPECIALTIES[spec], "reasoning": reasoning, "method": method,
        "doctors": doctors,
    }
