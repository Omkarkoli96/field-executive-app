"""
Field Executive Verification App
===================================
ADMIN:
  - Logs in with a username/password (HTTP Basic Auth)
  - Uploads Customer Data (Loan_ID, Customer_Name, Customer_Address)
  - Registers field executives (name + PIN) so only real, known
    executives can log visits under their own name
  - Downloads the visit log (Excel), filtered to today by default
  - Sees a short audit log of recent admin actions

FIELD EXECUTIVE:
  - Logs in with their registered name + PIN (set up by admin)
  - Types the Loan ID they're visiting -> customer name/address auto-fill
  - Captures location -> INSTANT Verified/Not Verified result
  - Every visit auto-saves into one shared log, with executive name as its
    own column — identity comes from the server-side session, not a
    value trusted from the browser
"""

import io
import os
import secrets
import time as time_module
from collections import defaultdict
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, Request, Form, Query, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from geopy.distance import geodesic
from sqlalchemy.orm import Session

from database import init_db, get_db
from models import Customer, Visit, Executive, AdminAction, Settings
from geocode import geocode_address
from auth import hash_pin, verify_pin

DEFAULT_VERIFICATION_RADIUS_KM = 1.0
LOW_ACCURACY_THRESHOLD_METERS = 100  # GPS readings worse than this are flagged for review
MAX_PLAUSIBLE_TRAVEL_KMH = 80        # faster implied speed between consecutive visits = suspicious
IST = ZoneInfo("Asia/Kolkata")

# Admin credentials — set these in Render's dashboard (or a local .env) in
# production. Falls back to a default for local dev only, so the app still
# runs out of the box, but that default must NOT be relied on once deployed.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme123")

# Signs the session cookie (holds the logged-in executive's identity).
# Set this in production too — falls back to a fixed dev value so the app
# still runs locally, but sessions won't be trustworthy across restarts
# (or across multiple server instances) until you set a real one.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-insecure-secret-change-me")

app = FastAPI(title="Field Executive Verification App")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

init_db()

security = HTTPBasic()

# --------------------------------------------------------------------------
# Basic in-memory rate limiting for admin login attempts.
# Keyed by client IP. Resets on restart and doesn't share state across
# multiple server instances — fine for a single small Render instance,
# not a substitute for a real rate-limiting service at larger scale.
# --------------------------------------------------------------------------
_failed_admin_attempts = defaultdict(list)  # ip -> [timestamps]
MAX_ADMIN_ATTEMPTS = 5
ADMIN_LOCKOUT_SECONDS = 15 * 60


def require_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    """Blocks anyone without the admin username/password. Applied to every
    /admin route so field executives can't reach uploads or the visit log
    just by knowing/guessing the URL. Also locks out an IP for 15 minutes
    after 5 failed attempts, to slow down password-guessing."""
    ip = request.client.host if request.client else "unknown"
    now = time_module.time()
    _failed_admin_attempts[ip] = [t for t in _failed_admin_attempts[ip] if now - t < ADMIN_LOCKOUT_SECONDS]

    if len(_failed_admin_attempts[ip]) >= MAX_ADMIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )

    valid_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_pass):
        _failed_admin_attempts[ip].append(now)
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def log_admin_action(db: Session, admin_username: str, action: str, detail: str = ""):
    db.add(AdminAction(admin_username=admin_username, action=action, detail=detail))
    db.commit()


def get_verification_radius_km(db: Session) -> float:
    setting = db.query(Settings).filter(Settings.key == "verification_radius_km").first()
    if setting and setting.value:
        try:
            return float(setting.value)
        except ValueError:
            pass
    return DEFAULT_VERIFICATION_RADIUS_KM


# --------------------------------------------------------------------------
# Field Executive flow
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
def login_submit(
    request: Request,
    executive_name: str = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    executive_name = executive_name.strip()
    pin = pin.strip()

    executive = db.query(Executive).filter(
        Executive.name == executive_name, Executive.active == "YES"
    ).first()

    if not executive or not verify_pin(pin, executive.pin_salt, executive.pin_hash):
        return RedirectResponse("/login?error=Invalid name or PIN.", status_code=303)

    # Identity now lives server-side in the signed session cookie — the
    # rest of the app trusts THIS, never a name typed into a form or URL.
    request.session["executive_name"] = executive.name
    return RedirectResponse("/visit", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/visit", response_class=HTMLResponse)
def visit_page(request: Request):
    executive_name = request.session.get("executive_name")
    if not executive_name:
        return RedirectResponse("/login?error=Please log in first.", status_code=303)
    return templates.TemplateResponse("visit.html", {"request": request, "executive": executive_name})


@app.get("/api/loan-lookup")
def api_loan_lookup(request: Request, loan_id: str = Query(...), db: Session = Depends(get_db)):
    """Called as the executive types a Loan ID, to auto-fill customer details."""
    if not request.session.get("executive_name"):
        return JSONResponse({"found": False, "error": "Not logged in."}, status_code=401)

    customer = db.query(Customer).filter(Customer.loan_id == loan_id.strip()).first()
    if not customer:
        return {"found": False}
    return {
        "found": True,
        "customer_name": customer.customer_name,
        "customer_address": customer.customer_address,
    }


@app.post("/api/visit")
def api_visit(
    request: Request,
    loan_id: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy: float = Form(0),
    db: Session = Depends(get_db),
):
    # Executive identity comes ONLY from the server-side session — never
    # from a form field the browser could be tricked into changing, so a
    # logged-in executive can't submit a visit under someone else's name.
    executive_name = request.session.get("executive_name")
    if not executive_name:
        return JSONResponse({"ok": False, "error": "Session expired. Please log in again."}, status_code=401)

    customer = db.query(Customer).filter(Customer.loan_id == loan_id.strip()).first()
    if not customer:
        return JSONResponse({"ok": False, "error": f"Loan ID {loan_id} not found in customer data."}, status_code=404)

    exec_coords = (latitude, longitude)

    # 1. Prefer admin-provided exact coordinates for the customer over
    #    geocoding the address text — far more reliable for landmark-style
    #    addresses that free geocoders can mismatch.
    if customer.customer_latitude is not None and customer.customer_longitude is not None:
        cust_coords = (customer.customer_latitude, customer.customer_longitude)
        cust_location_source = "GPS"
    else:
        cust_coords = geocode_address(db, customer.customer_address)
        cust_location_source = "Address (approx)" if cust_coords else "Not found"

    if not cust_coords:
        status = "COULD_NOT_GEOCODE"
        distance_km = None
    else:
        radius_km = get_verification_radius_km(db)
        distance_km = round(geodesic(exec_coords, cust_coords).km, 2)
        status = "VERIFIED" if distance_km <= radius_km else "NOT_VERIFIED"

    # 2. Flag GPS readings too imprecise to trust either way. A phone
    #    reporting 300m accuracy could read as "Verified" while the
    #    executive was actually well outside the radius, or vice versa.
    accuracy_flag = "LOW_ACCURACY" if accuracy and accuracy > LOW_ACCURACY_THRESHOLD_METERS else "OK"

    # 3. Impossible-travel check: compare against this SAME executive's
    #    most recent previous visit. If the implied speed between the two
    #    locations is beyond plausible travel, that's a strong signal
    #    something is off (e.g. GPS spoofing), independent of whether
    #    either individual reading looks "Verified".
    travel_flag = "N/A"
    previous_visit = (
        db.query(Visit)
        .filter(Visit.executive_name == executive_name)
        .order_by(Visit.created_at.desc())
        .first()
    )
    if previous_visit and previous_visit.executive_latitude is not None:
        now_utc = datetime.utcnow()
        elapsed_hours = max((now_utc - previous_visit.created_at).total_seconds() / 3600, 1 / 3600)  # floor 1s
        travel_distance_km = geodesic(
            (previous_visit.executive_latitude, previous_visit.executive_longitude),
            exec_coords,
        ).km
        implied_speed_kmh = travel_distance_km / elapsed_hours
        travel_flag = "SUSPICIOUS" if implied_speed_kmh > MAX_PLAUSIBLE_TRAVEL_KMH else "OK"

    visit_count = db.query(Visit).count()
    visit = Visit(
        visit_id=f"V{visit_count + 1:06d}",
        loan_id=loan_id.strip(),
        executive_name=executive_name,
        customer_name=customer.customer_name,
        customer_address=customer.customer_address,
        executive_latitude=round(latitude, 6),
        executive_longitude=round(longitude, 6),
        gps_accuracy_meters=round(accuracy, 1),
        distance_km=distance_km,
        status=status,
        accuracy_flag=accuracy_flag,
        travel_flag=travel_flag,
        customer_location_source=cust_location_source,
    )
    db.add(visit)
    db.commit()

    return {
        "ok": True,
        "visit_id": visit.visit_id,
        "status": status,
        "distance_km": distance_km,
        "accuracy_flag": accuracy_flag,
        "travel_flag": travel_flag,
    }


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db), admin_user: str = Depends(require_admin)):
    recent_actions_raw = db.query(AdminAction).order_by(AdminAction.created_at.desc()).limit(10).all()
    recent_actions = [{
        "action": a.action,
        "admin_username": a.admin_username,
        "detail": a.detail,
        "when": a.created_at.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %I:%M %p") if a.created_at else "",
    } for a in recent_actions_raw]
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "customer_count": db.query(Customer).count(),
        "visit_count": db.query(Visit).count(),
        "executives": db.query(Executive).filter(Executive.active == "YES").order_by(Executive.name).all(),
        "recent_actions": recent_actions,
        "current_radius_km": get_verification_radius_km(db),
    })


@app.post("/admin/upload")
def admin_upload(file: UploadFile = File(...), db: Session = Depends(get_db), admin_user: str = Depends(require_admin)):
    content = file.file.read()
    if file.filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    else:
        df = pd.read_excel(io.BytesIO(content), dtype=str).fillna("")

    required = ["Loan_ID", "Customer_Address"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return RedirectResponse(f"/admin?error=Missing columns: {missing}", status_code=303)

    has_name_col = "Customer_Name" in df.columns
    has_lat_col = "Customer_Latitude" in df.columns
    has_lon_col = "Customer_Longitude" in df.columns
    rows_processed = 0

    for _, row in df.iterrows():
        loan_id = str(row["Loan_ID"]).strip()
        if not loan_id:
            continue
        existing = db.query(Customer).filter(Customer.loan_id == loan_id).first()
        if existing is None:
            existing = Customer(loan_id=loan_id)
            db.add(existing)
        existing.customer_name = str(row["Customer_Name"]).strip() if has_name_col else ""
        existing.customer_address = str(row["Customer_Address"]).strip()

        existing.customer_latitude = None
        existing.customer_longitude = None
        if has_lat_col and has_lon_col:
            lat_raw = str(row["Customer_Latitude"]).strip()
            lon_raw = str(row["Customer_Longitude"]).strip()
            if lat_raw and lon_raw:
                try:
                    lat, lon = float(lat_raw), float(lon_raw)
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        existing.customer_latitude = lat
                        existing.customer_longitude = lon
                except ValueError:
                    pass  # falls back to geocoding the address at visit time

        rows_processed += 1

    db.commit()
    log_admin_action(db, admin_user, "UPLOAD_CUSTOMERS", f"{rows_processed} rows from '{file.filename}'")
    return RedirectResponse("/admin?ok=1", status_code=303)


@app.post("/admin/clear-customers")
def admin_clear_customers(db: Session = Depends(get_db), admin_user: str = Depends(require_admin)):
    """Backs up every customer record to an Excel file, THEN deletes them.
    The backup downloads automatically in the same response, so clearing
    data can never happen without a safety copy in hand. Does NOT touch
    the visit log — past visits stay on record even if the customer they
    refer to is cleared."""
    customers = db.query(Customer).all()
    rows = [{
        "Loan_ID": c.loan_id, "Customer_Name": c.customer_name, "Customer_Address": c.customer_address,
    } for c in customers]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Customers_Backup")
    buf.seek(0)

    count = len(customers)
    db.query(Customer).delete()
    log_admin_action(db, admin_user, "CLEAR_CUSTOMERS", f"{count} customers cleared (backup downloaded)")
    db.commit()

    timestamp = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Customer_Backup_before_clear_{timestamp}.xlsx"},
    )


# --------------------------------------------------------------------------
# Admin: executive management
# --------------------------------------------------------------------------

@app.post("/admin/settings/radius")
def admin_update_radius(
    radius_km: float = Form(...),
    db: Session = Depends(get_db),
    admin_user: str = Depends(require_admin),
):
    if radius_km <= 0 or radius_km > 50:
        return RedirectResponse("/admin?error=Radius must be between 0 and 50 km.", status_code=303)

    setting = db.query(Settings).filter(Settings.key == "verification_radius_km").first()
    if setting is None:
        setting = Settings(key="verification_radius_km")
        db.add(setting)
    setting.value = str(radius_km)
    log_admin_action(db, admin_user, "UPDATE_RADIUS", f"{radius_km} km")
    db.commit()
    return RedirectResponse("/admin?ok=radius_updated", status_code=303)


@app.post("/admin/executives/add")
def admin_add_executive(
    name: str = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
    admin_user: str = Depends(require_admin),
):
    name = name.strip()
    pin = pin.strip()

    if not name or not pin:
        return RedirectResponse("/admin?error=Name and PIN are required.", status_code=303)
    if not pin.isdigit() or not (4 <= len(pin) <= 8):
        return RedirectResponse("/admin?error=PIN must be 4-8 digits.", status_code=303)

    existing = db.query(Executive).filter(Executive.name == name).first()
    if existing and existing.active == "YES":
        return RedirectResponse(f"/admin?error=An executive named '{name}' already exists.", status_code=303)

    salt, pin_hash = hash_pin(pin)
    if existing:  # was soft-deleted — reactivate with the new PIN
        existing.pin_salt, existing.pin_hash, existing.active = salt, pin_hash, "YES"
    else:
        db.add(Executive(name=name, pin_salt=salt, pin_hash=pin_hash))

    log_admin_action(db, admin_user, "ADD_EXECUTIVE", f"'{name}'")
    db.commit()
    return RedirectResponse("/admin?ok=executive_added", status_code=303)


@app.post("/admin/executives/{executive_id}/remove")
def admin_remove_executive(
    executive_id: int,
    db: Session = Depends(get_db),
    admin_user: str = Depends(require_admin),
):
    """Soft-deletes an executive (can't log in anymore) rather than
    hard-deleting, so past visits still show a meaningful name."""
    executive = db.query(Executive).filter(Executive.id == executive_id).first()
    if executive:
        executive.active = "NO"
        log_admin_action(db, admin_user, "REMOVE_EXECUTIVE", f"'{executive.name}'")
        db.commit()
    return RedirectResponse("/admin?ok=executive_removed", status_code=303)


# --------------------------------------------------------------------------
# Admin-only: batch address verification (upload Customer Data + a Visited
# Data file — e.g. addresses collected offline/on paper — and get back a
# Verified/Not Verified result for each row). Completely separate from the
# live GPS-capture flow above; field executives have no route to this at
# all, it's gated by require_admin exactly like every other /admin page.
# --------------------------------------------------------------------------

def _read_table(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    if file.filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    return pd.read_excel(io.BytesIO(content), dtype=str).fillna("")


def _resolve_location(db: Session, row, lat_col: str, lon_col: str, address: str):
    """Returns ((lat, lon) or None, source_label). Prefers real GPS
    coordinates when both columns are present and valid; otherwise falls
    back to geocoding the address text (approximate, cached)."""
    lat_raw = str(row.get(lat_col, "")).strip() if lat_col in row else ""
    lon_raw = str(row.get(lon_col, "")).strip() if lon_col in row else ""

    if lat_raw and lon_raw:
        try:
            lat, lon = float(lat_raw), float(lon_raw)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon), "GPS"
        except ValueError:
            pass

    coords = geocode_address(db, address)
    if coords:
        return coords, "Address (approx)"
    return None, "Not found"


@app.get("/admin/batch-verify", response_class=HTMLResponse)
def admin_batch_verify_page(request: Request, db: Session = Depends(get_db), admin_user: str = Depends(require_admin)):
    return templates.TemplateResponse("batch_verify.html", {
        "request": request,
        "current_radius_km": get_verification_radius_km(db),
    })


@app.post("/admin/batch-verify/compare")
def admin_batch_verify_compare(
    customer_file: UploadFile = File(...),
    visited_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin_user: str = Depends(require_admin),
):
    try:
        customers_df = _read_table(customer_file)
    except Exception:
        return RedirectResponse("/admin/batch-verify?error=Could not read the Customer Data file.", status_code=303)

    try:
        visited_df = _read_table(visited_file)
    except Exception:
        return RedirectResponse("/admin/batch-verify?error=Could not read the Visited Data file.", status_code=303)

    cust_required = ["Loan_ID", "Customer_Address"]
    missing_cust = [c for c in cust_required if c not in customers_df.columns]
    visited_required = ["Loan_ID", "Visited_Address"]
    missing_visited = [c for c in visited_required if c not in visited_df.columns]

    if missing_cust or missing_visited:
        errors = []
        if missing_cust:
            errors.append(f"Customer file missing: {', '.join(missing_cust)}")
        if missing_visited:
            errors.append(f"Visited file missing: {', '.join(missing_visited)}")
        return RedirectResponse(f"/admin/batch-verify?error={'; '.join(errors)}", status_code=303)

    customers_df["Loan_ID"] = customers_df["Loan_ID"].str.strip()
    visited_df["Loan_ID"] = visited_df["Loan_ID"].str.strip()
    customers_by_id = {row["Loan_ID"]: row for _, row in customers_df.iterrows()}

    has_name_col = "Customer_Name" in customers_df.columns
    has_exec_col = "Executive_Name" in visited_df.columns
    has_date_col = "Visit_Date" in visited_df.columns
    radius_km = get_verification_radius_km(db)

    results = []
    for _, vrow in visited_df.iterrows():
        loan_id = vrow["Loan_ID"].strip()
        visited_address = vrow["Visited_Address"].strip()
        exec_name = vrow["Executive_Name"].strip() if has_exec_col else ""
        visit_date = vrow["Visit_Date"].strip() if has_date_col else ""

        cust = customers_by_id.get(loan_id)
        if cust is None:
            results.append({
                "Loan_ID": loan_id, "Executive_Name": exec_name, "Visit_Date": visit_date,
                "Customer_Name": "", "Customer_Address": "", "Visited_Address": visited_address,
                "Distance_KM": None, "Customer_Location_Source": "", "Visited_Location_Source": "",
                "Status": "LOAN_ID_NOT_FOUND",
            })
            continue

        customer_address = cust["Customer_Address"].strip()
        customer_name = cust["Customer_Name"].strip() if has_name_col else ""

        cust_coords, cust_source = _resolve_location(db, cust, "Customer_Latitude", "Customer_Longitude", customer_address)
        visited_coords, visited_source = _resolve_location(db, vrow, "Visited_Latitude", "Visited_Longitude", visited_address)

        if not cust_coords or not visited_coords:
            distance_km = None
            status = "COULD_NOT_GEOCODE"
        else:
            distance_km = round(geodesic(cust_coords, visited_coords).km, 2)
            status = "VERIFIED" if distance_km <= radius_km else "NOT_VERIFIED"

        results.append({
            "Loan_ID": loan_id, "Executive_Name": exec_name, "Visit_Date": visit_date,
            "Customer_Name": customer_name, "Customer_Address": customer_address,
            "Visited_Address": visited_address, "Distance_KM": distance_km,
            "Customer_Location_Source": cust_source, "Visited_Location_Source": visited_source,
            "Status": status,
        })

    result_df = pd.DataFrame(results)
    verified_count = sum(1 for r in results if r["Status"] == "VERIFIED")
    log_admin_action(db, admin_user, "BATCH_VERIFY", f"{len(results)} rows compared, {verified_count} verified (radius={radius_km}km)")

    buf = io.BytesIO()
    result_df.to_excel(buf, index=False, sheet_name="Verification_Result")
    buf.seek(0)
    timestamp = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Batch_Verification_Result_{timestamp}.xlsx"},
    )


@app.get("/admin/export")
def admin_export(
    date: str = Query(None, description="YYYY-MM-DD; defaults to today (IST) if omitted"),
    all_time: bool = Query(False, alias="all"),
    db: Session = Depends(get_db),
    admin_user: str = Depends(require_admin),
):
    def to_ist(dt):
        if not dt:
            return None
        # created_at is stored naive but is always UTC (see models.py) — attach
        # UTC explicitly before converting, otherwise Python assumes local time.
        return dt.replace(tzinfo=timezone.utc).astimezone(IST)

    query = db.query(Visit)
    filename_suffix = "All"

    if not all_time:
        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                target_date = datetime.now(IST).date()
        else:
            target_date = datetime.now(IST).date()

        # Build the target day's midnight-to-midnight window in IST, then
        # convert to naive UTC to match how created_at is stored, so the
        # filter lines up with the same day an executive would see on their
        # clock — not a UTC day that starts 5.5 hours earlier.
        day_start_ist = datetime.combine(target_date, time.min, tzinfo=IST)
        day_end_ist = datetime.combine(target_date, time.max, tzinfo=IST)
        day_start_utc = day_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
        day_end_utc = day_end_ist.astimezone(timezone.utc).replace(tzinfo=None)

        query = query.filter(Visit.created_at >= day_start_utc, Visit.created_at <= day_end_utc)
        filename_suffix = target_date.strftime("%Y-%m-%d")

    visits = query.order_by(Visit.created_at.desc()).all()

    rows = [{
        "Visit_ID": v.visit_id, "Loan_ID": v.loan_id, "Executive_Name": v.executive_name,
        "Customer_Name": v.customer_name, "Customer_Address": v.customer_address,
        "Customer_Location_Source": v.customer_location_source,
        "Executive_Latitude": v.executive_latitude, "Executive_Longitude": v.executive_longitude,
        "GPS_Accuracy_Meters": v.gps_accuracy_meters, "Accuracy_Flag": v.accuracy_flag,
        "Distance_KM": v.distance_km, "Status": v.status, "Travel_Flag": v.travel_flag,
        "Date": to_ist(v.created_at).strftime("%Y-%m-%d") if v.created_at else "",
        "Time": to_ist(v.created_at).strftime("%I:%M:%S %p") if v.created_at else "",
    } for v in visits]
    df = pd.DataFrame(rows)

    log_admin_action(db, admin_user, "EXPORT_VISITS", f"{len(rows)} rows, range={filename_suffix}")

    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Visits")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Visit_Log_{filename_suffix}.xlsx"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
