import pytest

from app.ai.dosage import build_schedule, extract_rules
from app.ai.rag import match_drug
from app.ai.safety import allergy_alert, check_red_flags
from app.ai.triage import classify_rules


@pytest.mark.parametrize("text,expected", [
    ("1 cp de 3 ori pe zi, după masă, 7 zile", {"dose_amount": 1, "times_per_day": 3, "duration_days": 7, "meal_relation": "dupa_masa"}),
    ("500 mg la 12 ore", {"dose_amount": 500, "dose_unit": "mg", "interval_hours": 12, "times_per_day": 2}),
    ("1-0-1", {"times_per_day": 2, "timing": ["dimineata", "seara"]}),
    ("2 pufuri la nevoie", {"dose_unit": "puf", "as_needed": True}),
    ("1 cp dimineața pe stomacul gol, 3 luni", {"timing": ["dimineata"], "meal_relation": "pe_stomacul_gol", "duration_days": 90}),
    ("o capsulă pe zi, 2 săptămâni", {"dose_unit": "capsula", "times_per_day": 1, "duration_days": 14}),
])
def test_dosage_rules(text, expected):
    d = extract_rules(text)
    for k, v in expected.items():
        assert d[k] == v, (k, d)


def test_schedule():
    assert build_schedule({"times_per_day": 2, "timing": []}) == ["08:00", "20:00"]
    assert build_schedule({"timing": ["seara"]}) == ["20:00"]
    assert build_schedule({"as_needed": True, "times_per_day": None}) == []


@pytest.mark.parametrize("text", [
    "Mă doare în piept de o oră", "nu pot să respir", "mi s-a strâmbat fața și vorbesc greu",
    "Vreau să mor", "mi s-au umflat buzele după pastilă", "am vomitat sânge", "Am o durere puternică în piept",
])
def test_red_flags_detected(text):
    assert check_red_flags(text).urgent


@pytest.mark.parametrize("text", ["Mă doare gâtul de două zile", "Cum iau ibuprofenul?", "am tuse ușoară"])
def test_no_false_alarm(text):
    assert not check_red_flags(text).urgent


def test_self_harm_message_has_helpline():
    assert "0800 801 200" in check_red_flags("nu mai vreau să trăiesc").message


def test_match_drug_and_allergy():
    d = match_drug("Amoxicilină 500 mg caps.")
    assert d and d.key == "amoxicilina"
    assert match_drug("Nurofen forte").key == "ibuprofen"
    assert match_drug("Medicament necunoscut") is None
    assert allergy_alert("Penicilină", d.key, d.classes, d.name)
    assert allergy_alert("", d.key, d.classes, d.name) is None
    assert allergy_alert("polen", d.key, d.classes, d.name) is None


def test_triage_rules():
    assert classify_rules("am palpitații") == "cardiologie"
    assert classify_rules("copilul are tuse") == "pediatrie"
    assert classify_rules("mă simt obosit") == "medicina_familie"
