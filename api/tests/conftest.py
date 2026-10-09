"""Testele rulează pe un PostgreSQL real (TEST_DATABASE_URL) și un LLM fals, determinist."""
import hashlib
import os

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://health4:health4@localhost:5432/health4_test"))

import pytest
from fastapi.testclient import TestClient

from app.ai import dosage, triage
from app.ai.ollama_client import LLMResult, get_llm
from app.config import get_settings
from app.db import Base, SessionLocal, engine, init_db
from app.main import app


class FakeLLM:
    """Răspunde după schema cerută, ca să testăm tot pipeline-ul fără Ollama."""

    model = "fake-llm"

    def __init__(self):
        self.calls = []

    def chat_json(self, messages, schema, *, model=None, temperature=None):
        self.calls.append(messages)
        props = schema.get("properties", {})
        user = messages[-1]["content"]
        if "dose_amount" in props:
            data = dosage.extract_rules(user)
        elif "specialty" in props and "reasoning" in props:
            data = {"specialty": triage.classify_rules(user), "reasoning": "test"}
        elif "medications" in props:
            n = user.count('"nume"')
            data = {"summary": "Rezumat test.",
                    "medications": [{"name": f"m{i}", "what_it_is_for": "pentru test", "how_to_take": "cum a zis medicul",
                                     "warnings": ["atenție test"]} for i in range(n)],
                    "questions_for_doctor": ["Întrebare test?"]}
        else:
            data = {"reply": f"Răspuns test la: {user[:30]}", "intent": "simptome" if "doare" in user else "sfat_general",
                    "suggested_specialty": triage.classify_rules(user) if "doare" in user else "niciuna"}
        return LLMResult(data=data, model=model or self.model, latency_ms=1)

    def embed(self, texts):
        dim = get_settings().embed_dim
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append([((h[i % 32] / 255) - 0.5) for i in range(dim)])
        return out


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def fake_llm():
    llm = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    yield llm
    app.dependency_overrides.pop(get_llm, None)


@pytest.fixture()
def client(fake_llm):
    with TestClient(app) as c:
        yield c


_counter = {"n": 0}


def _uniq() -> str:
    _counter["n"] += 1
    return f"{os.getpid()}{_counter['n']}"


@pytest.fixture()
def make_user(client):
    def _make(role="patient", **kw):
        u = _uniq()
        body = {"email": f"u{u}@test.ro", "password": "secret12", "first_name": "Test", "last_name": f"U{u}",
                "role": role, **kw}
        if role == "doctor":
            body.setdefault("specialty", "medicina_familie")
            body.setdefault("invite_code", get_settings().doctor_invite_code)
        r = client.post("/api/auth/register", json=body)
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _make
