from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from sqlalchemy.orm import Session

from models import GeocodeCache

_geolocator = Nominatim(user_agent="loan_recovery_executive_app", timeout=10)
_geocode_fn = RateLimiter(_geolocator.geocode, min_delay_seconds=1.2, max_retries=2, error_wait_seconds=5)


def _geocode_with_fallback(address: str):
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    for start in range(len(parts)):
        candidate = ", ".join(parts[start:])
        try:
            location = _geocode_fn(candidate)
        except Exception:
            location = None
        if location:
            return (location.latitude, location.longitude)
    return None


def geocode_address(db: Session, address: str):
    address = (address or "").strip()
    if not address:
        return None

    cached = db.query(GeocodeCache).filter(GeocodeCache.address == address).first()
    if cached and cached.found == "YES":
        return (cached.latitude, cached.longitude)
    if cached and cached.found == "NO":
        return None

    coords = _geocode_with_fallback(address)

    if cached is None:
        cached = GeocodeCache(address=address)
        db.add(cached)

    if coords:
        cached.latitude, cached.longitude = coords
        cached.found = "YES"
    else:
        cached.found = "NO"

    db.commit()
    return coords
