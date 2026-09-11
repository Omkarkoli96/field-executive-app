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

    created_at = Column(DateTime, default=datetime.utcnow)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"

    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True, index=True, nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    found = Column(String, default="PENDING")
