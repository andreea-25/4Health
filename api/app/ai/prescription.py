"""Interpretarea unei rețete pentru pacient.

Pași (fiecare verificabil separat):
 1. Fapte deterministe din DB: medicamente, doze, durată, diagnostic, alergiile pacientului.
 2. Potrivirea fiecărui medicament cu baza de cunoștințe + verificare alergii (fără LLM).
 3. Extragerea structurată a posologiei (LLM, cu fallback pe reguli) -> orar orientativ.
 4. LLM generează explicația în limbaj simplu, DOAR pe baza faptelor + contextului RAG.
 5. Post-procesare: textul original al medicului e păstrat neschimbat; disclaimer; surse.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Prescription
from . import dosage, rag
from .ollama_client import LLMClient
from .safety import DISCLAIMER, allergy_alert

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "what_it_is_for": {"type": "string"},
                    "how_to_take": {"type": "string"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "what_it_is_for", "how_to_take", "warnings"],
            },
        },
        "questions_for_doctor": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "medications", "questions_for_doctor"],
}

SYSTEM_PROMPT = """Ești Iris, asistentul medical al platformei 4Health. Explici unui pacient rețeta primită de la medic.
Reguli stricte:
- Scrii în limba română, simplu și cald, fără jargon.
- Folosești DOAR informațiile din RETETA și din CONTEXT. Nu inventa indicații, doze sau efecte.
- NU schimbi dozele, orele sau durata scrise de medic; le reformulezi doar mai clar.
- NU recomanzi alte medicamente și nu pui diagnostice.
- Dacă o informație lipsește din CONTEXT, scrie că pacientul să întrebe medicul sau farmacistul.
- "warnings": 1-3 atenționări scurte, cele mai importante, din CONTEXT.
- "questions_for_doctor": 1-3 întrebări utile pe care pacientul le-ar putea pune medicului.
Răspunzi doar cu JSON conform schemei."""


def _facts(rx: Prescription) -> dict:
    p = rx.patient
    return {
        "diagnostic": rx.diagnosis or "nespecificat",
        "data_eliberarii": rx.issued_at.isoformat(),
        "medic": f"Dr. {rx.doctor.user.last_name} {rx.doctor.user.first_name}",
        "alergii_pacient": p.allergies or "nedeclarate",
        "medicamente": [
            {"nume": i.drug_name, "concentratie_forma": i.concentration_form, "mod_administrare": i.administration,
             "cantitate": i.quantity, "durata": i.duration}
            for i in rx.items
        ],
    }


def interpret(db: Session, rx: Prescription, llm: LLMClient, model: str | None = None) -> dict:
    t0 = time.perf_counter()
    facts = _facts(rx)

    meds, context_hits, unmatched = [], [], []
    for item in rx.items:
        drug = rag.match_drug(item.drug_name)
        if drug:
            context_hits += [h for h in rag.drug_chunks(db, drug.key) if h.title not in {c.title for c in context_hits}]
        else:
            unmatched.append(item.drug_name)
        dose_text = " ".join(x for x in [item.administration, item.duration] if x)
        dose, method = dosage.extract(dose_text, llm) if dose_text else (dosage.empty(), "none")
        meds.append({
            "name": item.drug_name,
            "matched_drug": drug.key if drug else None,
            "doctor_instructions": {"concentration_form": item.concentration_form, "administration": item.administration,
                                    "quantity": item.quantity, "duration": item.duration},
            "dosage": dose,
            "dosage_method": method,
            "schedule": dosage.build_schedule(dose),
            "allergy_alert": allergy_alert(rx.patient.allergies, drug.key if drug else None,
                                           drug.classes if drug else [], drug.name if drug else item.drug_name),
        })

    user_msg = (
        "RETETA:\n" + json.dumps(facts, ensure_ascii=False, indent=1)
        + "\n\nCONTEXT (fișe medicamente):\n" + (rag.format_context(context_hits) or "(nu există informații)")
    )
    res = llm.chat_json([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
                        EXPLAIN_SCHEMA, model=model)
    expl = res.data if isinstance(res.data, dict) else {}

    # Aliniem explicațiile LLM la medicamentele reale (după index; numele rămâne cel din rețetă).
    llm_meds = expl.get("medications") or []
    for i, m in enumerate(meds):
        e = llm_meds[i] if i < len(llm_meds) else {}
        m["what_it_is_for"] = e.get("what_it_is_for") or "Întreabă medicul sau farmacistul pentru ce este acest medicament."
        m["how_to_take"] = e.get("how_to_take") or m["doctor_instructions"]["administration"]
        m["warnings"] = [w for w in (e.get("warnings") or []) if isinstance(w, str)][:3]

    return {
        "prescription_id": rx.id,
        "model": res.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": expl.get("summary", ""),
        "medications": meds,
        "questions_for_doctor": [q for q in (expl.get("questions_for_doctor") or []) if isinstance(q, str)][:3],
        "sources": [h.as_source() for h in context_hits],
        "grounding": {"unmatched_drugs": unmatched,
                      "note": "Medicamentele fără fișă în baza de cunoștințe sunt explicate doar pe baza rețetei."
                      if unmatched else ""},
        "disclaimer": DISCLAIMER,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }
