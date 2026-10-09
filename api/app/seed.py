"""Populează baza de date cu date demo și construiește indexul RAG.

    python -m app.seed            # adaugă datele demo (dacă lipsesc) + reindexează cunoștințele
    python -m app.seed --reset    # ȘTERGE tot și recreează schema
    python -m app.seed --rag-only # doar reindexare (după ce modifici knowledge/)
"""
import argparse
from datetime import date, timedelta

from sqlalchemy import select

from .ai import rag
from .ai.ollama_client import OllamaClient
from .db import Base, SessionLocal, engine, init_db
from .models import DoctorProfile, PatientProfile, Prescription, PrescriptionItem, Role, User
from .security import hash_password

DEMO_PASSWORD = "parola123"

DOCTORS = [
    ("Ionescu", "Andrei", "medicina_familie", "Cabinet Medicină de Familie Unirii", "București"),
    ("Pop", "Maria", "medicina_familie", "Cabinet Medicină de Familie Unirii", "București"),
    ("Georgescu", "Elena", "cardiologie", "Clinica 4Health - Calea Unirii", "București"),
    ("Stan", "Mihai", "endocrinologie", "Clinica 4Health - Calea Unirii", "București"),
    ("Matei", "Ana", "dermatologie", "Clinica 4Health - Calea Unirii", "București"),
    ("Radu", "Ioan", "gastroenterologie", "Clinica 4Health - Titan", "București"),
    ("Tudor", "Cristina", "pediatrie", "Clinica 4Health - Titan", "București"),
    ("Marin", "Raluca", "neurologie", "Clinica 4Health - Titan", "București"),
    ("Pavel", "Sorin", "orl", "Clinica 4Health - Calea Unirii", "București"),
    ("Florescu", "Gabriela", "pneumologie", "Clinica 4Health - Titan", "București"),
    ("Dumitru", "Vlad", "ortopedie", "Clinica 4Health - Titan", "București"),
    ("Constantin", "Irina", "psihiatrie", "Clinica 4Health - Calea Unirii", "București"),
]

PATIENTS = [
    # nume, prenume, cnp, alergii, afecțiuni
    ("Popescu", "Ion", "1800101400011", "penicilină", "hipertensiune arterială"),
    ("Dobre", "Elena", "2900215400022", "", "hipotiroidism"),
    ("Vasile", "Mihai", "1750630400033", "", "diabet zaharat tip 2"),
]


def _email(last: str, first: str, prefix: str = "") -> str:
    return f"{prefix}{first}.{last}@4health.ro".lower().replace("ă", "a").replace("ș", "s").replace("ț", "t")


def seed_users(db) -> None:
    if db.scalar(select(User).limit(1)):
        print("[seed] Utilizatori existenți - sar peste date demo.")
        return
    docs = []
    for last, first, spec, clinic, city in DOCTORS:
        u = User(email=_email(last, first, "dr."), password_hash=hash_password(DEMO_PASSWORD), role=Role.doctor,
                 first_name=first, last_name=last, doctor=DoctorProfile(specialty=spec, clinic=clinic, city=city))
        db.add(u)
        docs.append(u)
    db.flush()
    fam = docs[0].doctor

    pats = []
    for last, first, cnp, allergies, chronic in PATIENTS:
        u = User(email=_email(last, first), password_hash=hash_password(DEMO_PASSWORD), role=Role.patient,
                 first_name=first, last_name=last,
                 patient=PatientProfile(cnp=cnp, allergies=allergies, chronic_conditions=chronic, family_doctor_id=fam.id))
        db.add(u)
        pats.append(u)
    db.flush()

    today = date.today()
    rx = [
        (pats[0], fam, "B", "100001", "Faringită acută", today - timedelta(days=2), [
            ("Amoxicilină", "500 mg capsule", "1 capsulă la 8 ore", "20 capsule", "7 zile"),
            ("Paracetamol", "500 mg comprimate", "1 cp la nevoie, la febră peste 38,5°C, maxim 4 pe zi", "10 cp", ""),
        ]),
        (pats[0], docs[2].doctor, "B", "100002", "Hipertensiune arterială esențială", today - timedelta(days=30), [
            ("Enalapril", "10 mg comprimate", "1 cp dimineața", "30 cp", "30 de zile"),
            ("Amlodipină", "5 mg comprimate", "1-0-0", "30 cp", "30 de zile"),
        ]),
        (pats[1], docs[3].doctor, "B", "100003", "Hipotiroidism", today - timedelta(days=10), [
            ("Levotiroxină", "50 mcg comprimate", "1 cp dimineața pe stomacul gol, cu 30 de minute înainte de micul dejun", "90 cp", "3 luni"),
        ]),
        (pats[2], fam, "B", "100004", "Diabet zaharat tip 2; dislipidemie", today - timedelta(days=5), [
            ("Metformin", "850 mg comprimate", "1 cp de 2 ori pe zi, în timpul mesei", "60 cp", "30 de zile"),
            ("Atorvastatină", "20 mg comprimate", "1 cp seara", "30 cp", "30 de zile"),
        ]),
    ]
    for pu, doc, series, number, diag, issued, items in rx:
        db.add(Prescription(patient_id=pu.patient.id, doctor_id=doc.id, series=series, number=number,
                            clinic_unit=doc.clinic, diagnosis=diag, issued_at=issued,
                            items=[PrescriptionItem(drug_name=a, concentration_form=b, administration=c, quantity=d,
                                                    duration=e) for a, b, c, d, e in items]))
    db.commit()
    print(f"[seed] {len(docs)} medici, {len(pats)} pacienți, {len(rx)} rețete. Parolă demo: {DEMO_PASSWORD}")
    print(f"[seed] ex. pacient: {pats[0].email}   |   ex. medic: {docs[0].email}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--rag-only", action="store_true")
    ap.add_argument("--no-embed", action="store_true", help="Nu calcula embeddings (căutare doar lexicală)")
    args = ap.parse_args()

    if args.reset:
        Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as db:
        if not args.rag_only:
            seed_users(db)
        stats = rag.ingest(db, None if args.no_embed else OllamaClient())
        print(f"[seed] Index RAG: {stats['chunks']} fragmente, {stats['embedded']} cu embedding.")


if __name__ == "__main__":
    main()
