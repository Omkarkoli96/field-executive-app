"""
Field Executive App (database-backed, deployable version)
=============================================================
Same flow as the local version: login by name -> see allocated addresses
-> capture location -> instant Verified/Not Verified -> logged centrally.

The difference: visits and customer data live in a real database (safe
across restarts/redeploys), not a local Excel file. An Excel export is
still available any time via /admin/export.
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

app = FastAPI(title="Field Executive App")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

init_db()


# --------------------------------------------------------------------------
# Login / Dashboard / Visit
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/dashboard")
def login_submit(executive_name: str = Form(...)):
    executive_name = executive_name.strip()
    if not executive_name:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/dashboard?executive={executive_name}", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, executive: str = Query(...), db: Session = Depends(get_db)):
    executive = executive.strip()

    mine = db.query(Customer).filter(Customer.allocated_executive.ilike(executive)).all()
    visited_loan_ids = {v.loan_id for v in db.query(Visit).filter(Visit.executive_name.ilike(executive)).all()}

    pending = [c for c in mine if c.loan_id not in visited_loan_ids]
    done_count = len(mine) - len(pending)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "executive": executive,
        "pending": pending,
        "done_count": done_count,
        "total_count": len(mine),
    })


@app.get("/visit", response_class=HTMLResponse)
def visit_page(request: Request, executive: str = Query(...), loan_id: str = Query(...), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.loan_id == loan_id.strip()).first()
    if not customer:
        return templates.TemplateResponse("error.html", {"request": request, "message": f"Loan ID {loan_id} not found."})

    return templates.TemplateResponse("visit.html", {
        "request": request,
        "executive": executive,
        "loan_id": loan_id,
        "customer_address": customer.customer_address,
    })


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
        return JSONResponse({"ok": False, "error": f"Loan ID {loan_id} not found."}, status_code=404)

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
# Admin: upload customer data, export visit log
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

    required = ["Loan_ID", "Customer_Address", "Allocated_Executive"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return RedirectResponse(f"/admin?error=Missing columns: {missing}", status_code=303)

    for _, row in df.iterrows():
        loan_id = str(row["Loan_ID"]).strip()
        if not loan_id:
            continue
        existing = db.query(Customer).filter(Customer.loan_id == loan_id).first()
        if existing is None:
            existing = Customer(loan_id=loan_id)
            db.add(existing)
        existing.customer_name = row.get("Customer_Name", "")
        existing.customer_address = str(row["Customer_Address"]).strip()
        existing.allocated_executive = str(row["Allocated_Executive"]).strip()

    db.commit()
    return RedirectResponse("/admin?ok=1", status_code=303)


@app.get("/admin/export")
def admin_export(db: Session = Depends(get_db)):
    visits = db.query(Visit).order_by(Visit.created_at.desc()).all()
    rows = [{
        "Visit_ID": v.visit_id, "Loan_ID": v.loan_id, "Executive_Name": v.executive_name,
        "Customer_Address": v.customer_address,
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
