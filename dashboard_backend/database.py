"""
LeadRescuePro Dashboard Backend - Database Models
SQLite via SQLAlchemy
"""
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "leadrescuepro.db")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    pin_hash = Column(String(200), nullable=False)
    role = Column(String(20), default="caller")  # admin or caller
    full_name = Column(String(100))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    call_logs = relationship("CallLog", back_populates="caller")
    commissions = relationship("Commission", back_populates="caller")


class Prospect(Base):
    __tablename__ = "prospects"
    id = Column(Integer, primary_key=True, index=True)
    business_name = Column(String(200), index=True, nullable=False)
    phone = Column(String(30))
    city = Column(String(100))
    state = Column(String(20), default="TX")
    rating = Column(Float, nullable=True)
    reviews = Column(Integer, nullable=True)
    website = Column(String(300), nullable=True)
    score = Column(String(5), default="B")  # A, B, C
    status = Column(String(30), default="pending")  # pending, called, contacted, callback_set, dnc
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_contact = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    caller = relationship("User")
    call_logs = relationship("CallLog", back_populates="prospect")


class CallLog(Base):
    __tablename__ = "call_logs"
    id = Column(Integer, primary_key=True, index=True)
    prospect_id = Column(Integer, ForeignKey("prospects.id"), nullable=True)
    caller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    business_name = Column(String(200))
    phone = Column(String(30))
    disposition = Column(String(30), nullable=False)  # voicemail, no_answer, talked_interested, talked_not_interested, callback_set, dnc, wrong_number
    notes = Column(Text, nullable=True)
    duration_seconds = Column(Integer, default=0)
    recording_path = Column(String(500), nullable=True)
    transcript = Column(Text, nullable=True)
    call_time = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    prospect = relationship("Prospect", back_populates="call_logs")
    caller = relationship("User", back_populates="call_logs")


class Commission(Base):
    __tablename__ = "commissions"
    id = Column(Integer, primary_key=True, index=True)
    caller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    prospect_id = Column(Integer, ForeignKey("prospects.id"), nullable=True)
    deal_amount = Column(Float, default=1496.0)  # $997 setup + $499/mo
    commission_amount = Column(Float, default=100.0)  # Flat $100 per deal
    status = Column(String(20), default="pending")  # pending, paid
    deal_date = Column(DateTime, default=datetime.utcnow)
    paid_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    caller = relationship("User", back_populates="commissions")


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    business_name = Column(String(200), nullable=False, index=True)
    owner_name = Column(String(120), nullable=True)
    phone = Column(String(30), nullable=True)
    email = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(20), default="TX")
    onboarding_stage = Column(String(50), default="new")
    subscription_status = Column(String(30), default="active")
    setup_fee = Column(Float, default=997.0)
    monthly_revenue = Column(Float, default=499.0)
    usage_minutes = Column(Integer, default=0)
    usage_rate = Column(Float, default=0.5)
    started_at = Column(DateTime, default=datetime.utcnow)
    churned_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Recording(Base):
    __tablename__ = "recordings"
    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(Integer, ForeignKey("call_logs.id"), nullable=True)
    caller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path = Column(String(500), nullable=False)
    duration_seconds = Column(Integer, default=0)
    file_size = Column(Integer, default=0)
    transcribed = Column(Boolean, default=False)
    transcript = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if not existing:
            from auth import hash_pin
            admin = User(
                username="admin",
                pin_hash=hash_pin("4791"),
                role="admin",
                full_name="Fahim (Admin)",
                active=True
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
