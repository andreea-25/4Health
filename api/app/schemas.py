"""Scheme Pydantic pentru request/response."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import SPECIALTIES


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: Literal["patient", "doctor"] = "patient"
    # pacient
    cnp: str | None = None
    family_doctor_id: int | None = None
    allergies: str = ""
    # doctor
    specialty: str | None = None
    clinic: str = ""
    city: str = ""
    invite_code: str | None = None

    @field_validator("cnp")
    @classmethod
    def _cnp(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not (v.isdigit() and len(v) == 13):
            raise ValueError("CNP-ul trebuie să aibă 13 cifre.")
        return v

    @field_validator("specialty")
    @classmethod
    def _spec(cls, v: str | None) -> str | None:
        if v is not None and v not in SPECIALTIES:
            raise ValueError("Specializare necunoscută.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str


class UserOut(ORM):
    id: int
    email: str
    role: str
    first_name: str
    last_name: str


# ---------- Doctori ----------
class DoctorOut(BaseModel):
    id: int
    name: str
    specialty: str
    specialty_label: str
    clinic: str
    city: str


# ---------- Pacienți ----------
class PatientOut(BaseModel):
    id: int
    name: str
    email: str
    cnp: str | None
    allergies: str
    chronic_conditions: str
    family_doctor: DoctorOut | None


class PatientUpdate(BaseModel):
    allergies: str | None = None
    chronic_conditions: str | None = None
    family_doctor_id: int | None = None
    birth_date: date | None = None


# ---------- Rețete ----------
class PrescriptionItemIn(BaseModel):
    drug_name: str = Field(min_length=1, max_length=200)
    concentration_form: str = ""
    administration: str = ""
    quantity: str = ""
    duration: str = ""


class PrescriptionItemOut(ORM, PrescriptionItemIn):
    id: int


class PrescriptionIn(BaseModel):
    patient_id: int
    series: str = Field(min_length=1, max_length=20)
    number: str = Field(min_length=1, max_length=30)
    clinic_unit: str = Field(min_length=1, max_length=200)
    diagnosis: str = ""
    notes: str = ""
    issued_at: date
    items: list[PrescriptionItemIn] = Field(min_length=1)


class PrescriptionOut(BaseModel):
    id: int
    series: str
    number: str
    clinic_unit: str
    diagnosis: str
    notes: str
    issued_at: date
    created_at: datetime
    patient_name: str
    doctor_name: str
    doctor_specialty: str
    items: list[PrescriptionItemOut]


# ---------- AI ----------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None


class RecommendIn(BaseModel):
    symptoms: str = Field(min_length=3, max_length=2000)


class DosageIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
