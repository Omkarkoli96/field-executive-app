# Field Executive Verification App — Final Version

Built exactly to the architecture you specified:

**Admin**: uploads Customer Data (Loan ID, Customer Name, Customer
Address) — no pre-allocation needed. Can download the visit log (Excel)
any time.

**Field Executive**: enters their name, types the Loan ID they're
visiting (customer details auto-fill), taps capture, and gets an
**instant Verified/Not Verified result**. Every visit auto-saves into one
shared log, with the executive's name as its own column.

## What I tested before packaging this
- Upload works with just `Loan_ID` + `Customer_Address` (no allocation
  column required)
- Typing a real Loan ID auto-fills the correct customer name and address
- Typing a Loan ID not in the system returns a clean "not found" — no
  crash
- Any executive can check in against any Loan ID (confirmed: Sneha Patil
  checked in against a loan with no pre-assignment to her — this is
  intentional now, not a bug)
- Correct location → **Verified**, 0.00 km
- Wrong location → **Not verified**, correct real distance (17.36 km)
- A nonexistent Loan ID at capture time is rejected with a clear error,
  not saved as garbage data
- Excel export shows every visit with executive name, customer name,
  distance, and status, all in one sheet

## Files
| File | Purpose |
|---|---|
| `main.py` | The app — login, visit capture with auto-fill, instant verification, admin |
| `models.py` | Database tables (Customer, Visit, GeocodeCache) |
| `database.py` | DB connection (SQLite locally, Postgres in production) |
| `geocode.py` | Address → coordinates with caching and the flat/building fallback |
| `templates/` | Login, Visit (capture), Admin pages |
| `static/style.css` | Styling |
| `Customer_Data.xlsx` | Sample data, 20 loans, no allocation column |
| `runtime.txt` | Pins Python 3.11 (avoids the pandas build error we hit on Render) |
| `render.yaml` | Deployment blueprint (web service + free Postgres) |

## Run it locally

```
pip install -r requirements.txt
python main.py
```

Open **http://127.0.0.1:8000/admin**, upload `Customer_Data.xlsx`, then go
to **http://127.0.0.1:8000**, log in with any name, type a real Loan ID
(e.g. `LN000001`), and try the full capture flow.

## Deploying
Same process as before — GitHub (drag actual folders, not files, for
`templates`/`static`) → Render Blueprint → if pandas fails to build, add
`PYTHON_VERSION=3.11.9` as an environment variable in Render's dashboard
(this `runtime.txt` alone didn't do it for us last time — the environment
variable is what actually fixed it).

## Known limitations, honestly stated
- No login security — anyone can check in as any name, against any Loan
  ID. This is the architecture as specified; add authentication before
  wider rollout if that's a concern.
- Since there's no allocation, there's also no way to know who was
  *supposed* to visit a given customer — only who *did*. If you want
  accountability for missed visits, that needs allocation brought back in
  some form.
- Free Render tier sleeps after 15 minutes idle, and the free database
  expires after 90 days — same notes as before.
