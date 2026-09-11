"""
Field Executive Verification App
===================================
ADMIN:
  - Uploads Customer Data (Loan_ID, Customer_Name, Customer_Address) via /admin
  - Downloads the visit log (Excel) any time via /admin/export

FIELD EXECUTIVE:
  - Enters their name
  - Types the Loan ID they're visiting -> customer name/address auto-fill
  - Captures location -> INSTANT Verified/Not Verified result
  - Every visit auto-saves into one shared log, with executive name as its
    own column
"""

import io
import os
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, Request, Form, Query, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from geopy.distance import geodesic
from sqlalchemy.orm import Session

from database import init_db, get_db
from models import Customer, Visit
from geocode import geocode_address

VERIFICATION_RADIUS_KM = 1.0

app = FastAPI(title="Field Executive Verification App")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

init_db()


# --------------------------------------------------------------------------
# Field Executive flow
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/visit")
def login_submit(executive_name: str = Form(...)):
    executive_name = executive_name.strip()
    if not executive_name:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(f"/visit?executive={executive_name}", status_code=303)


@app.get("/visit", response_class=HTMLResponse)
def visit_page(request: Request, executive: str = Query(...)):
    return templates.TemplateResponse("visit.html", {"request": request, "executive": executive.strip()})


@app.get("/api/loan-lookup")
def api_loan_lookup(loan_id: str = Query(...), db: Session = Depends(get_db)):
    """Called as the executive types a Loan ID, to auto-fill customer details."""
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
    executive_name: str = Form(...),
    loan_id: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy: float = Form(0),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.loan_id == loan_id.strip()).first()
    if not customer:
        return JSONResponse({"ok": False, "error": f"Loan ID {loan_id} not found in customer data."}, status_code=404)

    cust_coords = geocode_address(db, customer.customer_address)
    exec_coords = (latitude, longitude)

    if not cust_coords:
        status = "COULD_NOT_GEOCODE"
        distance_km = None
    else:
        distance_km = round(geodesic(exec_coords, cust_coords).km, 2)
        status = "VERIFIED" if distance_km <= VERIFICATION_RADIUS_KM else "NOT_VERIFIED"

    visit_count = db.query(Visit).count()
    visit = Visit(
        visit_id=f"V{visit_count + 1:06d}",
        loan_id=loan_id.strip(),
        executive_name=executive_name.strip(),
        customer_name=customer.customer_name,
        customer_address=customer.customer_address,
        executive_latitude=round(latitude, 6),
        executive_longitude=round(longitude, 6),
        gps_accuracy_meters=round(accuracy, 1),
        distance_km=distance_km,
        status=status,
    )
    db.add(visit)
    db.commit()

    return {"ok": True, "visit_id": visit.visit_id, "status": status, "distance_km": distance_km}


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "customer_count": db.query(Customer).count(),
        "visit_count": db.query(Visit).count(),
    })


@app.post("/admin/upload")
def admin_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
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

    db.commit()
    return RedirectResponse("/admin?ok=1", status_code=303)


@app.get("/admin/export")
def admin_export(db: Session = Depends(get_db)):
    visits = db.query(Visit).order_by(Visit.created_at.desc()).all()
    rows = [{
        "Visit_ID": v.visit_id, "Loan_ID": v.loan_id, "Executive_Name": v.executive_name,
        "Customer_Name": v.customer_name, "Customer_Address": v.customer_address,
        "Executive_Latitude": v.executive_latitude, "Executive_Longitude": v.executive_longitude,
        "GPS_Accuracy_Meters": v.gps_accuracy_meters,
        "Distance_KM": v.distance_km, "Status": v.status,
        "Timestamp": v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else "",
    } for v in visits]
    df = pd.DataFrame(rows)

    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Visits")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Visit_Log.xlsx"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
