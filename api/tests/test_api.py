from datetime import date


def _rx(patient_id, number, items=None, **kw):
    return {"patient_id": patient_id, "series": "T", "number": number, "clinic_unit": "Cabinet test",
            "diagnosis": "Faringită", "issued_at": date.today().isoformat(),
            "items": items or [{"drug_name": "Amoxicilină", "concentration_form": "500 mg",
                                "administration": "1 capsulă la 8 ore", "quantity": "21", "duration": "7 zile"}], **kw}


def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"email": "Ana@Test.ro", "password": "secret12",
                                               "first_name": "Ana", "last_name": "Pop"})
    assert r.status_code == 201
    r = client.post("/api/auth/login", json={"email": "ana@test.ro", "password": "secret12"})
    assert r.status_code == 200 and r.json()["role"] == "patient"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "ana@test.ro"
    assert client.post("/api/auth/login", json={"email": "ana@test.ro", "password": "gresit"}).status_code == 401
    assert client.post("/api/auth/register", json={"email": "ana@test.ro", "password": "secret12",
                                                  "first_name": "A", "last_name": "B"}).status_code == 409


def test_doctor_needs_invite_code(client):
    r = client.post("/api/auth/register", json={"email": "fals@dr.ro", "password": "secret12", "first_name": "X",
                                               "last_name": "Y", "role": "doctor", "specialty": "cardiologie",
                                               "invite_code": "gresit"})
    assert r.status_code == 403


def test_roles_are_enforced(client, make_user):
    patient = make_user("patient")
    assert client.get("/api/doctor/patients", headers=patient).status_code == 403
    assert client.post("/api/doctor/prescriptions", headers=patient, json=_rx(1, "x")).status_code == 403
    assert client.get("/api/patient/prescriptions").status_code == 401


def test_prescription_flow_and_privacy(client, make_user):
    doctor = make_user("doctor")
    pat = make_user("patient", cnp="1990101123456", allergies="penicilină")
    other = make_user("patient")

    found = client.get("/api/doctor/patients/search", params={"cnp": "1990101123456"}, headers=doctor)
    assert found.status_code == 200
    pid = found.json()["id"]

    r = client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "001"))
    assert r.status_code == 201, r.text
    rx_id = r.json()["id"]
    assert client.post("/api/doctor/prescriptions", headers=doctor, json=_rx(pid, "001")).status_code == 409

    mine = client.get("/api/patient/prescriptions", headers=pat).json()
    assert [x["id"] for x in mine] == [rx_id]
    assert mine[0]["items"][0]["drug_name"] == "Amoxicilină"
    assert client.get("/api/patient/prescriptions", headers=other).json() == []
    assert client.get(f"/api/patient/prescriptions/{rx_id}", headers=other).status_code == 404
    assert pid in [p["id"] for p in client.get("/api/doctor/patients", headers=doctor).json()]

    # Interpretare AI: posologie structurată, orar, alertă de alergie, surse
    r = client.post(f"/api/ai/prescriptions/{rx_id}/interpret", headers=pat)
    assert r.status_code == 200, r.text
    out = r.json()
    med = out["medications"][0]
    assert med["matched_drug"] == "amoxicilina"
    assert med["dosage"]["times_per_day"] == 3 and med["dosage"]["duration_days"] == 7
    assert med["schedule"] == ["08:00", "16:00", "00:00"]
    assert med["allergy_alert"] and "penicilina" in med["allergy_alert"]
    assert med["doctor_instructions"]["administration"] == "1 capsulă la 8 ore"
    assert out["sources"] and out["disclaimer"]
    # a doua oară vine din cache
    assert client.post(f"/api/ai/prescriptions/{rx_id}/interpret", headers=pat).json()["cached"] is True
    assert client.post(f"/api/ai/prescriptions/{rx_id}/interpret", headers=other).status_code == 404


def test_recommend_and_red_flags(client, make_user):
    make_user("doctor", specialty="dermatologie")
    r = client.post("/api/ai/recommend", json={"symptoms": "Am o erupție pe piele care mă mănâncă de o săptămână"})
    body = r.json()
    assert body["specialty"] == "dermatologie" and body["doctors"]
    r = client.post("/api/ai/recommend", json={"symptoms": "Am o durere în piept care iradiază în braț"})
    assert r.json()["urgent"] is True and r.json()["doctors"] == []


def test_chat_saves_history_and_handles_emergency(client, make_user, fake_llm):
    pat = make_user("patient")
    r = client.post("/api/ai/chat", headers=pat, json={"message": "Cât trebuie să beau apă pe zi?"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    client.post("/api/ai/chat", headers=pat, json={"message": "Și vara?", "session_id": sid})
    s = client.get(f"/api/ai/chat/sessions/{sid}", headers=pat).json()
    assert len(s["messages"]) == 4

    n_calls = len(fake_llm.calls)
    r = client.post("/api/ai/chat", json={"message": "nu mai pot respira"})  # vizitator
    assert r.json()["urgent"] is True and "112" in r.json()["reply"]
    assert len(fake_llm.calls) == n_calls, "urgențele nu trebuie trimise la LLM"
    assert r.json()["session_id"] is None


def test_dosage_endpoint(client):
    r = client.post("/api/ai/dosage", json={"text": "1-0-1 după masă, 10 zile"})
    d = r.json()
    assert d["dosage"]["timing"] == ["dimineata", "seara"] and d["schedule"] == ["08:00", "20:00"]
