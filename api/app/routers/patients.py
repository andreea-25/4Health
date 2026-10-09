from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_patient
from ..models import DoctorProfile, PatientProfile, Prescription
from ..schemas import PatientOut, PatientUpdate, PrescriptionOut
from ..serializers import patient_out, prescription_out

router = APIRouter(prefix="/api/patient", tags=["patient"])


@router.get("/me", response_model=PatientOut)
def me(patient: PatientProfile = Depends(require_patient)):
    return patient_out(patient)


@router.patch("/me", response_model=PatientOut)
def update_me(
    data: PatientUpdate, patient: PatientProfile = Depends(require_patient), db: Session = Depends(get_db)
):
    changes = data.model_dump(exclude_unset=True)
    if changes.get("family_doctor_id") and not db.get(DoctorProfile, changes["family_doctor_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medic inexistent.")
    for k, v in changes.items():
        setattr(patient, k, v)
    db.commit()
    db.refresh(patient)
    return patient_out(patient)


@router.get("/prescriptions", response_model=list[PrescriptionOut])
def my_prescriptions(patient: PatientProfile = Depends(require_patient), db: Session = Depends(get_db)):
    q = (
        select(Prescription)
        .where(Prescription.patient_id == patient.id)
        .order_by(Prescription.issued_at.desc(), Prescription.id.desc())
    )
    return [prescription_out(r) for r in db.scalars(q)]


@router.get("/prescriptions/{rx_id}", response_model=PrescriptionOut)
def get_prescription(rx_id: int, patient: PatientProfile = Depends(require_patient), db: Session = Depends(get_db)):
    rx = db.get(Prescription, rx_id)
    if not rx or rx.patient_id != patient.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rețetă inexistentă.")
    return prescription_out(rx)
