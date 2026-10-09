from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_doctor
from ..models import SPECIALTIES, DoctorProfile, PatientProfile, Prescription, PrescriptionItem, User
from ..schemas import DoctorOut, PatientOut, PrescriptionIn, PrescriptionOut
from ..serializers import doctor_out, patient_out, prescription_out

public = APIRouter(prefix="/api", tags=["public"])
router = APIRouter(prefix="/api/doctor", tags=["doctor"])


# ---------------- Public ----------------
@public.get("/specialties")
def list_specialties():
    return [{"key": k, "label": v} for k, v in SPECIALTIES.items()]


@public.get("/doctors", response_model=list[DoctorOut])
def list_doctors(specialty: str | None = None, db: Session = Depends(get_db)):
    q = select(DoctorProfile).join(User).order_by(User.last_name)
    if specialty:
        q = q.where(DoctorProfile.specialty == specialty)
    return [doctor_out(d) for d in db.scalars(q)]


# ---------------- Doctor ----------------
@router.get("/me", response_model=DoctorOut)
def me(doctor: DoctorProfile = Depends(require_doctor)):
    return doctor_out(doctor)


def _my_patients_query(doctor: DoctorProfile):
    prescribed = select(Prescription.patient_id).where(Prescription.doctor_id == doctor.id)
    return (
        select(PatientProfile)
        .join(User)
        .where(or_(PatientProfile.family_doctor_id == doctor.id, PatientProfile.id.in_(prescribed)))
        .order_by(User.last_name)
    )


@router.get("/patients", response_model=list[PatientOut])
def my_patients(doctor: DoctorProfile = Depends(require_doctor), db: Session = Depends(get_db)):
    """Pacienții care l-au ales ca medic de familie sau cărora le-a emis rețete."""
    return [patient_out(p) for p in db.scalars(_my_patients_query(doctor))]


@router.get("/patients/search", response_model=PatientOut)
def find_patient_by_cnp(
    cnp: str = Query(min_length=13, max_length=13),
    doctor: DoctorProfile = Depends(require_doctor),
    db: Session = Depends(get_db),
):
    """Căutare exactă după CNP (nu listăm toți pacienții - confidențialitate)."""
    p = db.scalar(select(PatientProfile).where(PatientProfile.cnp == cnp))
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nu există pacient cu acest CNP.")
    return patient_out(p)


@router.post("/prescriptions", response_model=PrescriptionOut, status_code=201)
def create_prescription(
    data: PrescriptionIn, doctor: DoctorProfile = Depends(require_doctor), db: Session = Depends(get_db)
):
    patient = db.get(PatientProfile, data.patient_id)
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pacient inexistent.")
    rx = Prescription(
        doctor_id=doctor.id,
        patient_id=patient.id,
        series=data.series.strip(),
        number=data.number.strip(),
        clinic_unit=data.clinic_unit,
        diagnosis=data.diagnosis,
        notes=data.notes,
        issued_at=data.issued_at,
        items=[PrescriptionItem(**i.model_dump()) for i in data.items],
    )
    db.add(rx)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Există deja o rețetă cu această serie și număr.")
    db.refresh(rx)
    return prescription_out(rx)


@router.get("/prescriptions", response_model=list[PrescriptionOut])
def my_prescriptions(
    patient_id: int | None = None, doctor: DoctorProfile = Depends(require_doctor), db: Session = Depends(get_db)
):
    q = select(Prescription).where(Prescription.doctor_id == doctor.id).order_by(Prescription.issued_at.desc())
    if patient_id:
        q = q.where(Prescription.patient_id == patient_id)
    return [prescription_out(r) for r in db.scalars(q)]


@router.delete("/prescriptions/{rx_id}", status_code=204)
def delete_prescription(rx_id: int, doctor: DoctorProfile = Depends(require_doctor), db: Session = Depends(get_db)):
    rx = db.get(Prescription, rx_id)
    if not rx or rx.doctor_id != doctor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rețetă inexistentă.")
    db.delete(rx)
    db.commit()
