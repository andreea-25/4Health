"""Separarea agenților: cine are acces la ce și ce date ajung în prompt."""
from datetime import date, timedelta

from app.ai.agents import doctor as doctor_agent


def _rx(patient_id, number, drug, administration="1 cp pe zi", duration="30 de zile", issued=None):
    return {"patient_id": patient_id, "series": "AG", "number": number, "clinic_unit": "Cabinet",
            "diagnosis": "test", "issued_at": (issued or date.today()).isoformat(),
            "items": [{"drug_name": drug, "concentration_form": "", "administration": administration,
                       "quantity": "", "duration": duration}]}


def _setup(client, make_user, cnp):
    doctor = make_user("doctor")
    patient = make_user("patient", cnp=cnp, allergies="penicilină")
    pid = client.get("/api/doctor/patients/search", params={"cnp": cnp}, headers=doctor).json()["id"]
    return doctor, patient, pid


def test_roles_go_to_their_own_agent(client, make_user):
    doctor, patient = make_user("doctor"), make_user("patient")
    assert client.post("/api/ai/patient/chat", headers=doctor, json={"message": "salut"}).status_code == 403
    assert client.post("/api/ai/doctor/chat", headers=patient, json={"message": "salut"}).status_code == 403
    assert client.post("/api/ai/doctor/chat", json={"message": "salut"}).status_code == 401
    assert client.post("/api/ai/patient/chat", json={"message": "salut"}).status_code == 200  # vizitator


def test_doctor_sees_only_own_patients(client, make_user):
    doctor, _, pid = _setup(client, make_user, "2000101000001")
    other_doctor = make_user("doctor")
    # fără nicio rețetă și fără să fie medic de familie: nu are acces
    assert client.get(f"/api/ai/doctor/patients/{pid}/summary", headers=doctor).status_code == 404
    client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "A1", "Enalapril"))
    assert client.get(f"/api/ai/doctor/patients/{pid}/summary", headers=doctor).status_code == 200
    assert client.get(f"/api/ai/doctor/patients/{pid}/summary", headers=other_doctor).status_code == 404
    assert client.post("/api/ai/doctor/chat", headers=other_doctor,
                       json={"message": "rezumat", "patient_id": pid}).status_code == 404


def test_safety_check_allergy_and_interaction(client, make_user):
    doctor, _, pid = _setup(client, make_user, "2000101000002")
    client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "A2", "Enalapril"))
    r = client.post("/api/ai/doctor/safety-check", headers=doctor,
                    json={"patient_id": pid, "drugs": ["Amoxicilină", "Ibuprofen", "Medicament X"]}).json()
    assert r["allergy_alerts"] and r["allergy_alerts"][0]["drug"] == "Amoxicilină"
    pairs = {(i["drug_a"], i["drug_b"]) for i in r["interactions"]}
    assert ("Ibuprofen", "Enalapril") in pairs
    assert r["unknown_drugs"] == ["Medicament X"]


def test_expired_treatment_is_not_active(client, make_user):
    doctor, _, pid = _setup(client, make_user, "2000101000003")
    old = date.today() - timedelta(days=60)
    client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "A3", "Ibuprofen", duration="5 zile", issued=old))
    client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "A4", "Enalapril"))
    s = client.get(f"/api/ai/doctor/patients/{pid}/summary", headers=doctor).json()
    assert [m["drug_name"] for m in s["facts"]["active_medications"]] == ["Enalapril"]
    assert len(s["facts"]["prescription_history"]) == 2
    assert s["alerts"]["interactions"] == []   # ibuprofenul s-a terminat, deci nu mai interacționează
    assert s["summary"] == "Rezumat clinic test."


def test_doctor_chat_gets_patient_data_and_no_red_flag_filter(client, make_user, fake_llm):
    doctor, _, pid = _setup(client, make_user, "2000101000004")
    client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "A5", "Enalapril"))
    r = client.post("/api/ai/doctor/chat", headers=doctor,
                    json={"message": "Pacient cu durere în piept, ce trebuie să știu?", "patient_id": pid}).json()
    assert r["agent"] == "doctor" and r["urgent"] is False      # medicului nu i se răspunde „sunați la 112”
    system_prompt = fake_llm.calls[-1][0]["content"]
    assert "MEDIC" in system_prompt and "penicilină" in system_prompt and "Enalapril" in system_prompt


def test_patient_agent_prompt_has_only_own_data(client, make_user, fake_llm):
    pat = make_user("patient", allergies="polen")
    client.post("/api/ai/patient/chat", headers=pat, json={"message": "Ce sfaturi ai pentru alergii?"})
    system_prompt = fake_llm.calls[-1][0]["content"]
    assert "PACIENT" in system_prompt and "polen" in system_prompt


def test_interaction_word_boundary():
    # „calciu” din regula levotiroxinei nu trebuie să prindă amlodipina (clasa „blocant al canalelor de calciu”)
    assert doctor_agent._interaction("Amlodipină", "Levotiroxină") is None
    assert doctor_agent._interaction("Carbonat de calciu", "Levotiroxină")["severity"] == "moderata"
