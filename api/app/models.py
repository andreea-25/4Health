"""Schema bazei de date.

users ─┬─ doctor_profiles ──< prescriptions >── patient_profiles ─┬─ users
       │                         │                               │
       │                  prescription_items                     └─ family_doctor → doctor_profiles
       │                  ai_interpretations
       └─ chat_sessions ──< chat_messages

knowledge_chunks: baza de cunoștințe pentru RAG (text + embedding; pgvector sau JSON, vezi VECTOR_BACKEND)
"""
import enum
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import get_settings
from .db import Base

_settings = get_settings()
EMBED_DIM = _settings.embed_dim
USE_PGVECTOR = _settings.vector_backend == "pgvector"

if USE_PGVECTOR:
    from pgvector.sqlalchemy import Vector

    EmbeddingType = Vector(EMBED_DIM)
else:
    EmbeddingType = JSON  # listă de float-uri; similaritatea se calculează în Python


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    patient = "patient"
    doctor = "doctor"


# Specializările suportate (cheie -> nume afișat). Folosite și de AI pentru recomandări.
SPECIALTIES: dict[str, str] = {
    "medicina_familie": "Medicină de familie",
    "cardiologie": "Cardiologie",
    "dermatologie": "Dermatologie",
    "endocrinologie": "Endocrinologie",
    "gastroenterologie": "Gastroenterologie",
    "ginecologie": "Obstetrică-ginecologie",
    "neurologie": "Neurologie",
    "oftalmologie": "Oftalmologie",
    "orl": "ORL",
    "ortopedie": "Ortopedie",
    "pediatrie": "Pediatrie",
    "pneumologie": "Pneumologie",
    "psihiatrie": "Psihiatrie",
    "urologie": "Urologie",
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    doctor: Mapped["DoctorProfile | None"] = relationship(back_populates="user", uselist=False)
    patient: Mapped["PatientProfile | None"] = relationship(back_populates="user", uselist=False)

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}"


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    specialty: Mapped[str] = mapped_column(String(50), index=True)
    clinic: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    bio: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="doctor")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="doctor")


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    cnp: Mapped[str | None] = mapped_column(String(13), unique=True, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    family_doctor_id: Mapped[int | None] = mapped_column(
        ForeignKey("doctor_profiles.id", ondelete="SET NULL"), nullable=True
    )
    allergies: Mapped[str] = mapped_column(Text, default="")
    chronic_conditions: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="patient")
    family_doctor: Mapped[DoctorProfile | None] = relationship()
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="patient")


class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (UniqueConstraint("series", "number", name="uq_prescription_series_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series: Mapped[str] = mapped_column(String(20))
    number: Mapped[str] = mapped_column(String(30))
    clinic_unit: Mapped[str] = mapped_column(String(200))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id", ondelete="CASCADE"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctor_profiles.id", ondelete="RESTRICT"), index=True)
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    issued_at: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patient: Mapped[PatientProfile] = relationship(back_populates="prescriptions")
    doctor: Mapped[DoctorProfile] = relationship(back_populates="prescriptions")
    items: Mapped[list["PrescriptionItem"]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan", order_by="PrescriptionItem.id"
    )
    interpretations: Mapped[list["AIInterpretation"]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"))
    drug_name: Mapped[str] = mapped_column(String(200))           # DC / DCI
    concentration_form: Mapped[str] = mapped_column(String(200), default="")
    administration: Mapped[str] = mapped_column(Text, default="")  # text liber scris de medic
    quantity: Mapped[str] = mapped_column(String(100), default="")
    duration: Mapped[str] = mapped_column(String(100), default="")

    prescription: Mapped[Prescription] = relationship(back_populates="items")


class AIInterpretation(Base):
    """Cache + jurnal al interpretărilor AI (util și pentru analiză/evaluare ulterioară)."""

    __tablename__ = "ai_interpretations"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    output: Mapped[dict] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prescription: Mapped[Prescription] = relationship(back_populates="interpretations")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    drug: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    section: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Conversație nouă")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
