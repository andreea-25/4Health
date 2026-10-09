from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import DoctorProfile, PatientProfile, Role, User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Există deja un cont cu acest email.")

    user = User(
        email=email,
        password_hash=hash_password(data.password),
        role=Role(data.role),
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
    )

    if data.role == "doctor":
        if data.invite_code != get_settings().doctor_invite_code:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cod de invitație pentru medici invalid.")
        if not data.specialty:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Specializarea este obligatorie.")
        user.doctor = DoctorProfile(specialty=data.specialty, clinic=data.clinic, city=data.city)
    else:
        if data.cnp and db.scalar(select(PatientProfile).where(PatientProfile.cnp == data.cnp)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Există deja un pacient cu acest CNP.")
        if data.family_doctor_id and not db.get(DoctorProfile, data.family_doctor_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Medicul de familie nu există.")
        user.patient = PatientProfile(
            cnp=data.cnp, family_doctor_id=data.family_doctor_id, allergies=data.allergies
        )

    db.add(user)
    db.commit()
    return TokenOut(
        access_token=create_access_token(user.id, user.role.value), role=user.role.value, name=user.full_name
    )


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email sau parolă greșite.")
    return TokenOut(
        access_token=create_access_token(user.id, user.role.value), role=user.role.value, name=user.full_name
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(
        id=user.id, email=user.email, role=user.role.value, first_name=user.first_name, last_name=user.last_name
    )
