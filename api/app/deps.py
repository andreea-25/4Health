"""Dependințe FastAPI: utilizator curent și verificarea rolului."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import DoctorProfile, PatientProfile, Role, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def _user_from_token(creds: HTTPAuthorizationCredentials | None, db: Session) -> User | None:
    if creds is None:
        return None
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalid sau expirat.")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilizator inexistent.")
    return user


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    user = _user_from_token(creds, db)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autentificare necesară.")
    return user


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User | None:
    """Pentru rutele deschise și vizitatorilor (ex. chatul AI)."""
    return _user_from_token(creds, db)


def require_doctor(user: User = Depends(get_current_user)) -> DoctorProfile:
    if user.role != Role.doctor or user.doctor is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Doar medicii au acces.")
    return user.doctor


def require_patient(user: User = Depends(get_current_user)) -> PatientProfile:
    if user.role != Role.patient or user.patient is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Doar pacienții au acces.")
    return user.patient
