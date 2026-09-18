from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    loan_id = Column(String, unique=True, index=True, nullable=False)
    customer_name = Column(String)
    customer_address = Column(Text)
    # Optional precise coordinates — used instead of geocoding the address
    # text when present. Far more reliable for landmark-style addresses
    # (e.g. "XYZ Railway Station") that free geocoders often mismatch.
    customer_latitude = Column(Float, nullable=True)
    customer_longitude = Column(Float, nullable=True)


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True)
    visit_id = Column(String, unique=True, index=True)
    loan_id = Column(String, index=True, nullable=False)
    executive_name = Column(String, index=True)
    customer_name = Column(String)
    customer_address = Column(Text)

    executive_latitude = Column(Float)
    executive_longitude = Column(Float)
    gps_accuracy_meters = Column(Float)

    distance_km = Column(Float, nullable=True)
    status = Column(String)  # VERIFIED, NOT_VERIFIED, COULD_NOT_GEOCODE

    # Extra fraud-detection signals, independent of the distance check:
    accuracy_flag = Column(String, default="OK")  # OK / LOW_ACCURACY
    travel_flag = Column(String, default="OK")    # OK / SUSPICIOUS / N/A (first visit)
    customer_location_source = Column(String, default="")  # GPS / Address (approx)

    created_at = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    """Simple key-value store for admin-tunable settings (e.g. the
    verification radius) so they can be changed without touching code."""
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"

    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True, index=True, nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    found = Column(String, default="PENDING")


class Executive(Base):
    """A registered field executive who can log in with a PIN. Replaces
    the old model where anyone could type any name — only an admin can
    create these, and only the matching name+PIN combination logs in as
    that person."""
    __tablename__ = "executives"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True, nullable=False)
    pin_salt = Column(String, nullable=False)
    pin_hash = Column(String, nullable=False)
    active = Column(String, default="YES")  # YES / NO — soft-disable instead of deleting
    created_at = Column(DateTime, default=datetime.utcnow)


class AdminAction(Base):
    """Audit trail of admin actions — who did what, when. Shown on the
    admin page so there's accountability even when several people share
    (or each have) admin access."""
    __tablename__ = "admin_actions"

    id = Column(Integer, primary_key=True)
    admin_username = Column(String, nullable=False)
    action = Column(String, nullable=False)
    detail = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
