"""Strat de siguranță determinist (nu depinde de LLM).

1. Detectarea semnelor de urgență ("red flags") -> răspuns fix cu 112, fără să întrebăm modelul.
2. Verificarea alergiilor pacientului față de clasele medicamentelor din rețetă.

Regulile sunt deliberat conservatoare: preferăm fals-pozitive (trimitem la 112 inutil)
decât fals-negative. Recall-ul lor e măsurat în eval/ (task `triage`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text import normalize

RED_FLAGS: list[tuple[str, str]] = [
    (r"dur\w+ (\w+ ){0,2}(in|de|la) piept|durere toracica|(apasare|strangere|presiune) (in|pe) piept|(ma )?doare (in |la )?piept", "durere în piept"),
    (r"nu (mai )?pot (sa )?respir|ma sufoc|sufocare|(greutate|dificultat\w*) (mare )?(in|la|de) respir|respir (foarte )?greu|nu am aer", "dificultăți de respirație"),
    (r"paraliz|nu (imi )?(mai )?pot misca (mana|piciorul|bratul|o parte)|amorte\w* (pe )?(o parte|jumatate)|fata (mi )?(s-a )?stramb|gura stramba|vorbesc (greu|incurcat)|nu (mai )?pot vorbi", "semne posibile de AVC"),
    (r"lesin|am lesinat|pierdut cunostinta|inconstient|nu (mai )?raspunde", "pierderea cunoștinței"),
    (r"convulsi|criza epileptica|crize epileptice", "convulsii"),
    (r"(vomit|vars|voma|vomita\w*) (cu )?sange|sange (in|din) (voma|scaun|gura)|tusesc (cu )?sange|scaun(ul)? negru|sangerare (abundenta|puternica|masiva|care nu se opreste)", "sângerare gravă"),
    (r"sinucid|sa ma omor|sa imi iau viata|sa-mi iau viata|nu (mai )?vreau sa (mai )?traiesc|vreau sa mor|sa ma tai|autovatam|sa-mi fac rau|sa imi fac rau", "risc de autovătămare"),
    (r"(umflat|umflata|umflatura|umflare)\w* (la |pe |a )?(fata|buze|buzele|limba|gat|gatul)|(fata|buzele|limba|gatul) (mi )?(s-a|s-au|se) umfl|edem quincke|soc anafilactic|anafila", "reacție alergică severă"),
    (r"supradoz|(am|a) luat prea mult\w* (pastile|comprimate|medicament)|intoxica|am inghitit (o sticla|tot flaconul)", "posibilă intoxicație"),
    (r"(cea mai (mare|puternica|groaznica) durere de cap)|durere de cap (brusca|fulgeratoare)", "cefalee bruscă severă"),
    (r"febra (de |peste |mai mare de )?(40|41|42)|(bebelus|sugar)\w* (are |cu )?febra", "febră foarte mare / la sugar"),
]
_COMPILED = [(re.compile(p), label) for p, label in RED_FLAGS]

EMERGENCY_MESSAGE = (
    "⚠️ Ce descrii poate fi o urgență medicală. Te rog sună ACUM la 112 sau mergi la cea mai apropiată "
    "unitate de primiri urgențe. Nu aștepta un răspuns online și nu conduce singur dacă te simți rău."
)
SELF_HARM_MESSAGE = (
    "Îmi pare foarte rău că treci prin asta și mă bucur că ai scris. Nu ești singur(ă). Dacă ești în pericol "
    "imediat, sună la 112. Poți vorbi gratuit și confidențial cu cineva la linia de prevenție a suicidului "
    "0800 801 200. Dacă poți, spune-i și unei persoane apropiate cum te simți acum."
)

DISCLAIMER = (
    "Informațiile sunt generale și nu înlocuiesc consultul medical. Respectă întotdeauna indicațiile "
    "medicului și ale farmacistului."
)


@dataclass
class SafetyResult:
    urgent: bool
    flags: list[str] = field(default_factory=list)
    message: str | None = None


def check_red_flags(text: str) -> SafetyResult:
    t = normalize(text)
    flags = [label for rx, label in _COMPILED if rx.search(t)]
    if not flags:
        return SafetyResult(urgent=False)
    msg = SELF_HARM_MESSAGE if "risc de autovătămare" in flags else EMERGENCY_MESSAGE
    return SafetyResult(urgent=True, flags=flags, message=msg)


def allergy_alert(allergies: str, drug_key: str | None, drug_classes: list[str], drug_label: str) -> str | None:
    """Avertisment dacă alergiile declarate se potrivesc cu medicamentul sau clasa lui."""
    if not allergies.strip():
        return None
    a = normalize(allergies)
    terms = [t for t in [drug_key, *drug_classes] if t]
    hits = [t for t in terms if normalize(t) in a or (len(t) > 6 and normalize(t)[:6] in a)]
    if hits:
        return (f"Atenție: în profil ai declarat alergie la „{allergies.strip()}”, iar {drug_label} face parte din "
                f"categoria {', '.join(sorted(set(hits)))}. Discută cu medicul sau farmacistul ÎNAINTE să îl iei.")
    return None
