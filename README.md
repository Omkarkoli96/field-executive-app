# Field Executive App — Deployable Version

Same tested flow as before — login by name, see allocated visits, capture
location, instant Verified/Not Verified — but now backed by a real
database, so it's safe to actually deploy online.

**Everything below was tested locally before packaging**: upload,
dashboard filtering, instant verification (both Verified and Not Verified
cases), dashboard updating after a visit, and Excel export — all confirmed
working with real requests against a running server.

## What's new vs. the local-only version
- Data lives in a database (Postgres in production, SQLite for local
  testing) instead of Excel files on disk — **survives restarts and
  redeploys**, which plain files don't.
- New `/admin` page: upload/refresh `Customer_Data.xlsx` without touching
  code, and download the current visit log as Excel any time.

## Part 1 — Test it locally first

```
pip install -r requirements.txt
python main.py
```

Open **http://127.0.0.1:8000/admin**, upload the included
`Customer_Data.xlsx` (sample data, 20 loans across Ravi Kumar / Sneha
Patil / Amit Shinde), then go to **http://127.0.0.1:8000** and log in as
one of them to try the real flow.

## Part 2 — Deploy to Render (free, no cloud account needed)

### Step 1 — Put the code on GitHub
1. Create a free account at https://github.com if needed.
2. Create a new repository (e.g. `field-executive-app`).
3. Upload every file in this package to it (drag-and-drop works fine on
   GitHub's website — no command line required).

### Step 2 — Create a Render account
https://render.com — free, no credit card needed for the free tier.

### Step 3 — Deploy using the blueprint
1. In Render: **New +** → **Blueprint**
2. Connect GitHub, select your repository
3. Render reads `render.yaml` and sets up both the **web service** and a
   **free PostgreSQL database** automatically, already connected
4. Click **Apply** — first deploy takes a few minutes

### Step 4 — Load your real data and go live
Once deployed, you'll have a URL like:
```
https://field-executive-app.onrender.com
```
1. Go to `https://field-executive-app.onrender.com/admin` and upload your
   real `Customer_Data.xlsx` (needs `Loan_ID`, `Customer_Address`,
   `Allocated_Executive` columns)
2. Send executives the base URL — they log in with just their name
3. Check `/admin` any time to download the current visit log as Excel

### Free tier notes
- The free web service **sleeps after 15 minutes of inactivity**, taking
  ~30 seconds to wake on the next visit. Upgrade to Render's paid tier
  ($7/month) to remove this if it becomes a daily-use tool.
- The free Postgres database **expires after 90 days** — you'll need to
  upgrade or migrate to a fresh free database before then. This is a
  Render policy, not something in the code.

## Still no login security
As before — anyone with the link can check in as any executive, and the
`/admin` page has no password. Fine for testing with your own team; add
real authentication before wider rollout or before using real customer
data at scale. Data privacy is also worth confirming with your company's
policy before going live with real customer addresses and executive
locations.
