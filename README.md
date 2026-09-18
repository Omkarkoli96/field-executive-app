# Field Executive Verification App

**Admin**: uploads Customer Data, registers field executives (name +
PIN), downloads the visit log (defaults to today only), and sees an
audit trail of recent admin actions. Protected by username/password with
rate-limited login attempts.

**Field Executive**: logs in with their own registered name + PIN, types
the Loan ID they're visiting (customer details auto-fill), taps capture,
and gets an **instant Verified/Not Verified result**. Every visit
auto-saves into one shared log — the executive's identity comes from
their server-side login session, not from anything the browser sends, so
one executive can't log a visit under someone else's name.

## What changed in this security-hardening pass, and why

| Before | Now | Why |
|---|---|---|
| Any typed name logged a visit under that name | Executives are registered by admin (name + PIN); login is verified server-side | Anyone could log a visit under a colleague's name — no accountability |
| Executive identity came from a URL/form value | Identity comes from a signed session cookie the server sets at login | A URL or form value can be edited by the browser; a signed server-side session cannot |
| No record of who uploaded/cleared/exported data | Every admin action is logged (who, what, when) and shown on the Admin page | If two people share admin access, you need to know who did what |
| "Clear all customers" just deleted, no undo | Automatically downloads an Excel backup in the same click, before deleting | A misclick used to be unrecoverable |
| Admin login had no attack-slowing | Locks out an IP for 15 minutes after 5 failed attempts | Slows down password-guessing (not a full solution — see limitations) |

## What I tested before packaging this
- Registering an executive with a duplicate name is rejected
- Registering with a non-numeric or too-short/long PIN is rejected
- Logging in with the wrong PIN is rejected and redirects with a clear error
- Logging in with the correct name+PIN sets a session and redirects to `/visit`
- `/visit` and `/api/visit` both redirect/reject (401) with no session
- **Spoofing test**: while logged in as one executive, manually sending a
  different `executive_name` value in the visit request is ignored — the
  visit is still recorded under the real logged-in executive's name,
  confirmed directly in the database
- Removing an executive (soft-delete) makes their PIN stop working
  immediately, while past visits under their name stay intact and their
  name still appears correctly in the audit log
- Admin page renders the active executive list and recent activity log
  correctly, with no errors
- `/admin`, `/admin/upload`, `/admin/export`, `/admin/clear-customers`,
  and the executive-management routes all reject requests without valid
  admin credentials (401)
- Visit log export: default (today only), a specific past date, and
  all-time all return the correct, correctly-filtered rows
- Distance/verification math still correct (unchanged from before)

## Files
| File | Purpose |
|---|---|
| `main.py` | The app — executive login/session, visit capture, admin (customer upload, executive management, audit log, exports) |
| `auth.py` | PIN hashing (PBKDF2 + per-record salt) |
| `models.py` | Database tables (Customer, Visit, Executive, AdminAction, GeocodeCache) |
| `database.py` | DB connection (SQLite locally, Postgres in production) |
| `geocode.py` | Address → coordinates with caching and the flat/building fallback |
| `templates/` | Home, Login (name+PIN), Visit (capture), Admin pages |
| `static/style.css` | Styling |
| `Customer_Data.xlsx` | Sample data |
| `runtime.txt` | Pins Python 3.11 (avoids the pandas build error on Render) |
| `render.yaml` | Deployment blueprint (web service + free Postgres) |

## Run it locally

```
pip install -r requirements.txt
python main.py
```

1. Open **http://127.0.0.1:8000/admin** (login: `admin` / `changeme123`
   by default locally)
2. Upload `Customer_Data.xlsx`
3. Register a field executive (e.g. name `Ravi Kumar`, PIN `1234`)
4. Go to **http://127.0.0.1:8000**, log in as that executive, and try the
   full capture flow with a real Loan ID (e.g. `LN01`)

## Deploying
Same process as before — GitHub (drag actual folders, not files, for
`templates`/`static`) → Render Blueprint → if pandas fails to build, add
`PYTHON_VERSION=3.11.9` as an environment variable in Render's dashboard.

**Important: this version adds new database columns and tables**
(`Customer.customer_latitude`/`customer_longitude`, `Visit.accuracy_flag`/
`travel_flag`/`customer_location_source`, and a new `settings` table).
SQLAlchemy's `create_all` only creates missing *tables*, not missing
*columns* on tables that already exist — so if you're upgrading an
already-deployed database rather than starting fresh, you'll need to
either drop and recreate the Render Postgres database, or add these
columns manually. For a fresh deploy (new database), this happens
automatically and needs no action.

## Core verification accuracy — second hardening pass

The first pass secured *who* can log a visit (executive PIN logins,
sessions, audit trail). This pass improves *how good the verification
call itself is* — since the whole point of the app is telling genuine
visits from fake ones:

| Feature | What it does | Why |
|---|---|---|
| **Customer GPS override** | Upload optional `Customer_Latitude`/`Customer_Longitude` columns — used directly instead of geocoding the address text | Free geocoding can mismatch landmark-style addresses (e.g. "XYZ Railway Station"), silently making the distance check wrong. Pre-surveyed exact coordinates remove that error entirely. |
| **Low-accuracy flag** | Every visit records an `Accuracy_Flag` (`OK` / `LOW_ACCURACY`) based on the phone's own GPS accuracy reading (>100m = flagged) | A "Verified" result from a phone with 300m GPS accuracy isn't trustworthy — this flags it instead of hiding it. |
| **Impossible-travel flag** | Every visit records a `Travel_Flag` — compares distance and time against that same executive's previous visit, flags implied speeds over 80 km/h as `SUSPICIOUS` | Two visits 120km apart, 5 minutes apart, is physically impossible — a strong fraud signal that distance-to-one-customer checks alone can't catch. |
| **Configurable radius** | Admin can change the verification radius (default 1 km) from the Admin page, no code changes needed | 1 km is loose in dense areas like Nigdi — a wrong building could still read as Verified. Admin can tighten it as needed. |

**Tested**: uploaded a customer with exact GPS coordinates and confirmed
the app used them (not the geocoder); logged a visit with weak (200m)
GPS accuracy and confirmed it was flagged `LOW_ACCURACY` even though the
distance check alone said Verified; logged two visits 120km apart
seconds apart and confirmed `Travel_Flag: SUSPICIOUS`; changed the
radius from 1km to 0.3km and confirmed the same GPS point flipped from
Verified to Not Verified accordingly.

## Batch address verification (admin only)

A new page, `/admin/batch-verify`, lets admin upload **Customer Data**
and a **Visited Data** file (e.g. addresses collected offline, on paper,
or from another system) together, and get an instant Verified/Not
Verified result for every row — without needing the field executive to
use the live GPS-capture flow at all.

- Matches rows by `Loan_ID`
- Geocodes both addresses (cached), or uses exact coordinates directly
  if `Customer_Latitude`/`Customer_Longitude` and/or
  `Visited_Latitude`/`Visited_Longitude` columns are provided
- Uses the same configurable verification radius as the live flow
  (default 1 km): **distance ≤ radius → VERIFIED, greater → NOT_VERIFIED**
- Downloads the result as an Excel file immediately, and logs the action
  (row count, verified count) in the admin audit trail
- Completely separate from the live GPS-capture Visit log — doesn't
  touch it

**Admin-only, verified two ways**: it lives under `/admin/...` and uses
the same `require_admin` check as every other admin route — tested that
both the page and the compare endpoint return 401 without admin login.
There is no link to it anywhere in the field-executive login or visit
pages, so an executive has no path to it even by guessing the URL.

## Environment variables (set real values before going live)
- `ADMIN_USERNAME`, `ADMIN_PASSWORD` — admin login. Falls back to
  `admin`/`changeme123` if unset, which is NOT safe to leave in
  production since it's printed in this README.
- `SESSION_SECRET` — signs executive login sessions. `render.yaml` has
  Render auto-generate this (`generateValue: true`), so you shouldn't
  need to set it manually, but if you ever change it, every executive
  gets logged out.
- `DATABASE_URL` — set automatically by Render's Postgres add-on.

## Visit log export
`/admin/export` defaults to **today's visits only** (midnight to
midnight, India time). On the Admin page:
- **Download Today's Visits** — the default
- **Pick a date** — a specific past day (`/admin/export?date=YYYY-MM-DD`)
- **Download All-Time Visit Log** — everything (`/admin/export?all=true`)

## Known limitations, honestly stated
- **Free Render database expires after 90 days.** This is the single
  biggest risk before real company data goes in — upgrade to a paid
  Postgres plan before launch, or all customer/visit/executive data will
  be deleted automatically.
- **Free Render tier sleeps after 15 minutes idle.** First request after
  that takes 30-60 seconds — noticeable to a field executive waiting at
  a customer's door. A paid "always-on" plan removes this.
- **GPS can be spoofed** by fake-GPS apps on the executive's phone. This
  app has no way to detect that from browser geolocation alone — a real
  fix needs either managed devices or a more sophisticated detection
  layer, which is a bigger project.
- **The rate limiter is in-memory**, so it resets on every server
  restart/redeploy and doesn't share state if you ever scale to multiple
  server instances. Fine for a single small Render instance; not a
  substitute for a dedicated rate-limiting service at larger scale.
- **No allocation**: the app still can't tell you who was *supposed* to
  visit a customer, only who *did*. A missed visit is invisible unless
  cross-checked manually.
- **Free address geocoding is approximate for landmark names** (e.g.
  "XYZ Railway Station") — it can snap to a generic locality centroid
  instead of the exact place. Worth knowing if verification accuracy for
  borderline (near-1km) results matters for disciplinary decisions.
- **Legal/compliance**: this app tracks employee GPS location and stores
  customer PII (name, address). Depending on your jurisdiction and
  business, this may touch labor-law consent requirements and data
  protection obligations (e.g. India's DPDP Act 2023). I'm not a lawyer —
  worth a quick check with whoever handles compliance before wide
  rollout.
