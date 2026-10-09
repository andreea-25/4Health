"""Conversii model ORM -> schemă de răspuns."""
from .models import SPECIALTIES, DoctorProfile, PatientProfile, Prescription
from .schemas import DoctorOut, PatientOut, PrescriptionItemOut, PrescriptionOut


def doctor_out(d: DoctorProfile) -> DoctorOut:
    return DoctorOut(
        id=d.id,
        name=f"Dr. {d.user.last_name} {d.user.first_name}",
        specialty=d.specialty,
        specialty_label=SPECIALTIES.get(d.specialty, d.specialty),
        clinic=d.clinic,
        city=d.city,
    )


def patient_out(p: PatientProfile) -> PatientOut:
    return PatientOut(
        id=p.id,
        name=p.user.full_name,
        email=p.user.email,
        cnp=p.cnp,
        allergies=p.allergies,
        chronic_conditions=p.chronic_conditions,
        family_doctor=doctor_out(p.family_doctor) if p.family_doctor else None,
    )


def prescription_out(r: Prescription) -> PrescriptionOut:
    return PrescriptionOut(
        id=r.id,
        series=r.series,
        number=r.number,
        clinic_unit=r.clinic_unit,
        diagnosis=r.diagnosis,
        notes=r.notes,
        issued_at=r.issued_at,
        created_at=r.created_at,
        patient_name=r.patient.user.full_name,
        doctor_name=f"Dr. {r.doctor.user.last_name} {r.doctor.user.first_name}",
        doctor_specialty=SPECIALTIES.get(r.doctor.specialty, r.doctor.specialty),
        items=[PrescriptionItemOut.model_validate(i) for i in r.items],
    )
