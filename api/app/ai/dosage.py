"""Extragerea structurată a posologiei din textul liber scris de medic.

Exemplu:  "1 cp de 3 ori pe zi, după masă, 7 zile"
      ->  {dose_amount: 1, dose_unit: "comprimat", times_per_day: 3, duration_days: 7,
           meal_relation: "dupa_masa", timing: [], interval_hours: null, as_needed: false}

Două implementări, comparate în eval/:
- `extract_rules`: baseline cu expresii regulate (rapid, determinist, fragil la formulări noi)
- `extract_llm`  : LLM cu ieșire structurată (JSON Schema) prin Ollama
"""
from __future__ import annotations

import re
from typing import Any

from .ollama_client import LLMClient, LLMError
from .text import normalize

UNITS = ["comprimat", "capsula", "mg", "g", "ml", "picatura", "plic", "puf", "lingurita", "aplicare", "fiola", "unitate"]
TIMINGS = ["dimineata", "pranz", "seara", "noaptea"]
MEAL = ["inainte_de_masa", "dupa_masa", "in_timpul_mesei", "pe_stomacul_gol", "nespecificat"]

DOSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dose_amount": {"type": ["number", "null"], "description": "cantitatea pe priză"},
        "dose_unit": {"type": ["string", "null"], "enum": UNITS + [None]},
        "times_per_day": {"type": ["integer", "null"]},
        "interval_hours": {"type": ["number", "null"]},
        "duration_days": {"type": ["integer", "null"]},
        "timing": {"type": "array", "items": {"type": "string", "enum": TIMINGS}},
        "meal_relation": {"type": "string", "enum": MEAL},
        "as_needed": {"type": "boolean"},
    },
    "required": ["dose_amount", "dose_unit", "times_per_day", "interval_hours", "duration_days",
                 "timing", "meal_relation", "as_needed"],
}

FIELDS = list(DOSAGE_SCHEMA["properties"].keys())

# ------------------------------------------------------------------ baseline
_NUM_WORDS = {"o": 1, "un": 1, "una": 1, "unu": 1, "doua": 2, "doi": 2, "trei": 3, "patru": 4, "cinci": 5,
              "sase": 6, "sapte": 7, "opt": 8, "zece": 10, "jumatate": 0.5, "1/2": 0.5}
_NUM = r"(\d+(?:[.,]\d+)?|1/2|o|un|una|unu|doua|doi|trei|patru|cinci|sase|sapte|opt|zece|jumatate)"
_UNIT_PATTERNS = [
    (r"cp|comprimat\w*|tb|tablet\w*|pastil\w*", "comprimat"),
    (r"caps\w*", "capsula"),
    (r"mg", "mg"), (r"ml", "ml"), (r"g", "g"),
    (r"pic\w*", "picatura"), (r"plic\w*", "plic"), (r"puf\w*|inhalat\w*", "puf"),
    (r"lingurit\w*", "lingurita"), (r"aplicar\w*", "aplicare"), (r"fiol\w*", "fiola"), (r"ui|unitati", "unitate"),
]


def _num(s: str) -> float:
    s = s.replace(",", ".")
    return float(_NUM_WORDS[s]) if s in _NUM_WORDS else float(s)


def _unit(s: str) -> str | None:
    for pat, name in _UNIT_PATTERNS:
        if re.fullmatch(pat, s):
            return name
    return None


def empty() -> dict:
    return {"dose_amount": None, "dose_unit": None, "times_per_day": None, "interval_hours": None,
            "duration_days": None, "timing": [], "meal_relation": "nespecificat", "as_needed": False}


def extract_rules(text: str) -> dict:
    t = normalize(text)
    out = empty()

    # Schema românească "1-0-1" (dimineața-prânz-seara) sau "1-1-1-1"
    m = re.search(r"\b(\d(?:[.,]5)?)\s*-\s*(\d(?:[.,]5)?)\s*-\s*(\d(?:[.,]5)?)(?:\s*-\s*(\d(?:[.,]5)?))?\b", t)
    if m:
        parts = [p for p in m.groups() if p is not None]
        names = TIMINGS[: len(parts)] if len(parts) == 4 else ["dimineata", "pranz", "seara"]
        vals = [_num(p) for p in parts]
        out["timing"] = [n for n, v in zip(names, vals) if v > 0]
        out["times_per_day"] = len(out["timing"])
        nonzero = [v for v in vals if v > 0]
        if nonzero and len(set(nonzero)) == 1:
            out["dose_amount"] = nonzero[0]
        t = t[: m.start()] + " " + t[m.end():]

    # Doză + unitate:  "1 cp", "500 mg", "doua capsule", "1/2 comprimat"
    for m in re.finditer(_NUM + r"\s*(cp|comprimat\w*|tb|tablet\w*|pastil\w*|caps\w*|mg|ml|g|pic\w*|plic\w*|puf\w*|inhalat\w*|lingurit\w*|aplicar\w*|fiol\w*|ui|unitati)\b", t):
        unit = _unit(m.group(2))
        if unit and out["dose_unit"] is None:
            out["dose_amount"] = out["dose_amount"] or _num(m.group(1))
            out["dose_unit"] = unit
            break

    # Frecvență:  "de 3 ori pe zi", "3x/zi", "o data pe zi", "de doua ori/zi"
    m = re.search(r"(?:de\s+)?" + _NUM + r"\s*(?:x|ori|data|dati)\s*(?:pe|/|la|in)?\s*(?:zi|24)", t)
    if m and out["times_per_day"] is None:
        out["times_per_day"] = int(_num(m.group(1)))
    m = re.search(r"(?:la|din|fiecare)\s+(?:fiecare\s+)?(\d+)\s*(?:ore|h)\b", t)
    if m:
        out["interval_hours"] = float(m.group(1))
        if out["times_per_day"] is None and 24 % int(m.group(1)) == 0:
            out["times_per_day"] = 24 // int(m.group(1))

    if out["times_per_day"] is None and re.search(r"(?:pe|/)\s*zi\b|zilnic", t) and not re.search(r"\bmax", t):
        out["times_per_day"] = 1

    # Momentul zilei
    if not out["timing"]:
        found = []
        if re.search(r"diminea", t): found.append("dimineata")
        if re.search(r"pranz|amiaza", t): found.append("pranz")
        if re.search(r"\bseara", t): found.append("seara")
        if re.search(r"noapte|culcare", t): found.append("noaptea")
        out["timing"] = found
        if found and out["times_per_day"] is None:
            out["times_per_day"] = len(found)

    # Durată: "7 zile", "timp de o saptamana", "2 saptamani", "o luna"
    m = re.search(_NUM + r"\s*(?:de\s+)?(zile|zi)\b(?!\s*(?:pe|/))", t)
    if m and not re.search(r"(pe|/)\s*$", t[: m.start()]):
        out["duration_days"] = int(_num(m.group(1)))
    else:
        m = re.search(_NUM + r"\s*(?:de\s+)?(saptamani|saptamana)", t)
        if m:
            out["duration_days"] = int(_num(m.group(1)) * 7)
        else:
            m = re.search(_NUM + r"\s*(luni|luna)\b", t)
            if m:
                out["duration_days"] = int(_num(m.group(1)) * 30)

    # Relația cu masa
    if re.search(r"inainte(a)? (de )?(masa|mese|mesei|micul dejun)", t): out["meal_relation"] = "inainte_de_masa"
    elif re.search(r"dupa (masa|mese|mesei|mancare|micul dejun)", t): out["meal_relation"] = "dupa_masa"
    elif re.search(r"(in timpul|la) (mesei|masa|mese)|cu (mancare|masa)", t): out["meal_relation"] = "in_timpul_mesei"
    elif re.search(r"stomacul gol|a jeun|pe nemancate", t): out["meal_relation"] = "pe_stomacul_gol"

    out["as_needed"] = bool(re.search(r"la nevoie|daca (e|este) nevoie|la durere|la febra|\bsos\b|in caz de", t))
    return out


# ------------------------------------------------------------------ LLM
SYSTEM_PROMPT = """Extragi posologia dintr-o indicație medicală scrisă în română și răspunzi DOAR cu JSON.
Reguli:
- dose_amount = cantitatea luată o dată (ex. "2 cp" -> 2). null dacă lipsește.
- dose_unit: comprimat, capsula, mg, g, ml, picatura, plic, puf, lingurita, aplicare, fiola, unitate (null dacă lipsește).
- times_per_day = de câte ori pe zi. "la 8 ore" -> 3, "1-0-1" -> 2.
- interval_hours doar dacă textul spune explicit "la X ore".
- duration_days: "7 zile" -> 7, "2 săptămâni" -> 14, "o lună" -> 30. null dacă lipsește.
- timing: momentele zilei menționate explicit (dimineata, pranz, seara, noaptea). "1-0-1" -> ["dimineata","seara"].
- meal_relation: inainte_de_masa, dupa_masa, in_timpul_mesei, pe_stomacul_gol sau nespecificat.
- as_needed = true doar pentru "la nevoie", "la durere", "la febră".
Nu inventa valori care nu apar în text."""

FEW_SHOT = [
    ("1 cp de 2 ori pe zi, după masă, 10 zile",
     {"dose_amount": 1, "dose_unit": "comprimat", "times_per_day": 2, "interval_hours": None, "duration_days": 10,
      "timing": [], "meal_relation": "dupa_masa", "as_needed": False}),
    ("500 mg la 8 ore",
     {"dose_amount": 500, "dose_unit": "mg", "times_per_day": 3, "interval_hours": 8, "duration_days": None,
      "timing": [], "meal_relation": "nespecificat", "as_needed": False}),
]


def _sanitize(d: dict) -> dict:
    out = empty()
    for k in FIELDS:
        if k in d:
            out[k] = d[k]
    if out["dose_unit"] not in UNITS: out["dose_unit"] = None
    if out["meal_relation"] not in MEAL: out["meal_relation"] = "nespecificat"
    out["timing"] = [x for x in (out["timing"] or []) if x in TIMINGS]
    for k in ("times_per_day", "duration_days"):
        if isinstance(out[k], (int, float)) and out[k] > 0:
            out[k] = int(out[k])
        else:
            out[k] = None
    for k in ("dose_amount", "interval_hours"):
        out[k] = float(out[k]) if isinstance(out[k], (int, float)) and out[k] > 0 else None
    out["as_needed"] = bool(out["as_needed"])
    return out


def build_messages(text: str) -> list[dict]:
    import json
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for q, a in FEW_SHOT:
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": json.dumps(a, ensure_ascii=False)})
    msgs.append({"role": "user", "content": text})
    return msgs


def extract_llm(text: str, llm: LLMClient, model: str | None = None) -> dict:
    res = llm.chat_json(build_messages(text), DOSAGE_SCHEMA, model=model, temperature=0)
    return _sanitize(res.data if isinstance(res.data, dict) else {})


def extract(text: str, llm: LLMClient | None) -> tuple[dict, str]:
    """LLM cu fallback la reguli. Returnează (posologie, metoda)."""
    if llm is not None:
        try:
            return extract_llm(text, llm), "llm"
        except LLMError:
            pass
    return extract_rules(text), "rules"


# ------------------------------------------------------------------ orar
_DEFAULT_HOURS = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "14:00", "20:00"],
                  4: ["08:00", "12:00", "16:00", "20:00"]}
_TIMING_HOURS = {"dimineata": "08:00", "pranz": "14:00", "seara": "20:00", "noaptea": "22:00"}


def build_schedule(d: dict) -> list[str]:
    """Orar orientativ de administrare (pacientul îl poate ajusta)."""
    if d.get("as_needed"):  # medicamentele „la nevoie” nu au orar fix
        return []
    if d.get("timing"):
        return [_TIMING_HOURS[t] for t in TIMINGS if t in d["timing"]]
    if d.get("interval_hours"):
        step = int(d["interval_hours"])
        if 0 < step <= 24:
            return [f"{(8 + i * step) % 24:02d}:00" for i in range(max(1, 24 // step))]
    n = d.get("times_per_day")
    return _DEFAULT_HOURS.get(n, []) if n else []
