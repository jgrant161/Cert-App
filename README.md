# Certificate Review

AI-assisted review of sales-and-use-tax exemption and resale certificates.
Upload the folder of certificates a client's customers have provided. The app
reads every PDF, builds the certificate schedule (one row per company, certificate
type, and state, with multijurisdiction forms split into one row per state), flags
what needs follow-up, and exports the Excel deliverable. You can then ask
questions about the client's certificates in plain English.

It turns the process we ran by hand on the AEI and Additive Manufacturing
engagements into a product a firm can run for every client.

## What it does

| Step | What happens |
|---|---|
| **Intake** | Drag in PDFs or a `.zip` (nested zips are fine). Each upload is a *batch* ("9.30.26 Certs"). Files already on file are recognised by content hash and skipped, so when a client re-sends an overlapping folder, only the new files are added. Byte-identical copies saved under a new name are linked to the original, never re-read, and still listed on the reconciliation. |
| **Filename parsing** | Both naming conventions seen so far: `Code Company Type State` and `Company - Type (State)`, plus `Multijurisdiction` and `TBD` tokens. The filename is treated as a hint. The document always wins. |
| **AI reading** | Claude reads each PDF and reports what is on the page: form, purchaser, certificate type, signature, expiry, and **every state line on multistate grids** with the exact text and a classification (registration number, home-state number, FEIN, N/A, blank, dashed, "See attached", other text). |
| **Review policy** | The judgment calls (does "N/A" count? a home-state number? an FEIN on every line? a number followed by "exempt per nexus rules"?) are per-client toggles. Change one and the schedule re-scores instantly. Nothing is re-read. |
| **Flags** | Wrong state in the filename, multijurisdiction files that are really single-state, TBD files resolved, no qualifying state, the same number repeated across states, nexus disclaimers, expired or expiring certificates, unsigned forms, entity-based exemptions, exact and content duplicates, low-confidence reads, and anything else the reader noticed (for example extra certificates bundled on later pages). |
| **Reviewer workspace** | Each certificate opens side by side with its PDF, showing every state line, whether it made the schedule and why, and one-click include/exclude overrides. Review status and notes carry through to the export. |
| **Excel export** | Overview (including the policy used), Schedule (colour-coded), Summary by State, Summary by Company, Follow-Up, Excluded Lines, Reconciliation (every source file and the rows it produced, with totals), and Batches. |
| **Ask about this client** | Questions such as "Which customers have no qualifying registration?" or "What do we need to request before year end?" are answered from the engagement's data, citing source files. |

## Running it

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...       # without a key the app runs in demo mode
python -m certapp serve                   # http://127.0.0.1:8000
```

**First launch:** the app asks you to **create the first administrator** (your name, email
and a password of at least 12 characters). After that, everyone signs in. Existing clients
and certificates are kept.

**Sharing with your team on the office network:** run `python -m certapp serve --share`.
The window prints an address such as `http://192.168.1.25:8000` that teammates on the same
network (or VPN) can open while the app is running on your computer. Windows may ask
whether to allow Python through the firewall: choose **Private networks**. Teammates need
a user account (see **Sign-in and roles**) before they can see anything.

**Deleting a client:** open the client, scroll to the bottom, open **Delete this client**,
type the client name to confirm, and click **Delete client permanently**. This removes the
certificates, schedule, notes and questions for that client and cannot be undone.

Batch mode, straight to Excel with no browser:

```bash
python -m certapp run "9.30.26 Certs.zip" --client "Additive Manufacturing, LLC" -o schedule.xlsx
```

Running `run` again with the same `--client` adds the new files to that client's
existing engagement and writes the updated workbook.

**Demo for prospects** (no client data and no API key needed):

```bash
python scripts/seed_demo.py && python -m certapp serve
```

This creates a sample engagement covering each situation the real reviews turned
up: nexus-disclaimer grids, home-state numbers, "Wayfair Ruling" lines, blank
Streamlined forms, a wrong-state filename, a TBD file, a federal-instrumentality
exemption, an expired certificate, a bundled multi-certificate PDF, a duplicate,
and a second batch.

| Setting | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Turns on AI reading and Q&A |
| `CERTAPP_EXTRACTOR` | `auto` | `claude` forces AI (e.g. with an `ant auth login` profile); `demo` forces offline mode |
| `CERTAPP_DATA_DIR` | `./data` | SQLite database and stored PDFs |
| `DATABASE_URL` | — | PostgreSQL connection; when set, used instead of SQLite |
| `CERTAPP_FILES_DIR` | `<data dir>/files` | Where uploaded PDFs are stored |
| `CERTAPP_ENV` | — | `production` turns on HTTPS-only cookies and the setup code |
| `CERTAPP_SECRET_KEY` | generated locally | Signs sign-in cookies; required in production |
| `CERTAPP_BASE_URL` | — | Public address, for Microsoft sign-in and invitation links |
| `CERTAPP_MS_TENANT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET` | — | Turn on Microsoft 365 sign-in |
| `CERTAPP_WORKERS` | `4` | Certificates read in parallel |

Tests: `pytest`

## Sign-in and roles

| Role | Sees | Can do |
|---|---|---|
| **Firm administrator** | Every client | Everything: create and delete clients, add users, assign staff, view all activity |
| **Firm staff** | Only clients assigned to them | Upload, review, overrides, review policy, questions, Excel export |
| **Client administrator** | Only their own company | View the schedule and follow-ups, download Excel, add and remove their company's users |
| **Client user** | Only their own company | View the schedule and follow-ups, download Excel |

- **Client separation** is enforced on every page and download. A client that a user may not
  see answers "Page not found", so the app does not even reveal which clients exist.
  `tests/test_auth.py` walks every client route as every role to prove it.
- **Firm staff sign in with Microsoft 365** once it is configured (see `docs/DEPLOY_AZURE.md`).
  Only accounts in the firm's own Microsoft tenant that an administrator has added can get in,
  and two-step sign-in follows the firm's Microsoft 365 rules.
- **Clients sign in with a password** they choose from a one-time invitation link (valid 7 days).
  The app does not send email yet: an administrator copies the link from the **Users** page
  and emails it. The same page creates a new link when someone forgets their password.
- Passwords are stored as salted scrypt hashes. Five wrong passwords lock the account for
  15 minutes. Deactivating a user, changing their role or changing a password signs them out
  everywhere immediately.
- **Activity** records sign-ins, uploads, exports, documents opened, overrides, policy changes
  and user changes.
- Locked out? `python -m certapp create-admin --email you@firm.com --name "Your Name"` prints a
  link to set a password; `python -m certapp sign-in-link --email someone@x.com` makes a new link
  for an existing password user.

## Cloud hosting

The app runs unchanged on a server: set `DATABASE_URL` to a PostgreSQL database and
`CERTAPP_FILES_DIR` to persistent storage, and build the `Dockerfile`. `docs/DEPLOY_AZURE.md`
walks through Azure App Service, Azure Database for PostgreSQL, Azure Files and the Microsoft
365 app registration; `.env.example` lists every setting. In production mode
(`CERTAPP_ENV=production`) the app requires a secret signing key, sends cookies only over
HTTPS, and requires a setup code on the first-run page.

The test suite runs on SQLite and on PostgreSQL (`CERTAPP_TEST_DATABASE_URL=postgresql://...
pytest`), and GitHub Actions runs both on every push.

## How it is built

```
certapp/
  filenames.py        filename conventions -> company / type / state hint
  ingest.py           zip/PDF intake, batches, hash de-duplication
  extraction/
    schema.py         what the reader reports for one certificate (strict JSON schema)
    claude.py         Claude reads the PDF (structured output, adaptive thinking)
    demo.py           offline fallback for demos
  pipeline.py         reads pending certificates in parallel; one bad file never stops a batch
  rules.py            review policy, line decisions, flags, duplicates -> schedule rows
  export.py           Excel workbook
  assistant.py        follow-up Q&A over the engagement data
  auth.py             roles, permissions, passwords, sessions, invitation links
  microsoft.py        Microsoft 365 (Entra ID) sign-in for firm staff
  db.py               storage: SQLite locally, PostgreSQL in the cloud
  web/                FastAPI app + templates
```

The core design choice is the split between **reading** and **deciding**. The
reader reports what is written on each line and never decides whether a state
counts. `rules.py` makes that decision from the engagement's policy plus reviewer
overrides. That keeps the reviewer's judgment visible and reversible: every
excluded line is listed with the reason it was excluded, and flipping a policy
toggle re-scores the whole engagement without another read.

Model: `claude-opus-5-5` at high effort for reading certificates (dense scanned
grids are the hard part) and medium effort for Q&A, with server-side refusal
fallback enabled.

## Taking it to market: what's next

Phase 1 (done): sign-in with four roles, client separation, an activity log, Microsoft 365
sign-in for staff, and the pieces needed to host it (PostgreSQL, container, Azure guide).

Next:

1. **Client portal polish (Phase 2)**: two-step sign-in for client password accounts, sending invitation emails from the app, and a client-facing summary page.
2. **Security program**: retention schedules, a tested restore, and a SOC 2 path when larger clients ask for it. Certificates contain tax IDs.
3. **Ongoing certificate management** (the recurring-revenue feature): an expiry calendar, automated renewal requests to the seller's customers, and a customer-facing upload link so new certificates arrive already in the right engagement.
4. **Coverage testing**: match certificates against the client's sales by customer and ship-to state to show exempt sales that lack a valid certificate. That is the audit-exposure number a CFO cares about.
5. **Cost and scale**: route large backlogs through the Message Batches API (about half the cost, asynchronous) and keep interactive uploads on the standard API.
6. **Accuracy program**: keep the AEI and Additive Manufacturing schedules as a labelled test set and measure every prompt or model change against them before it ships.

Packaging options: a per-engagement service (the firm runs it and delivers the
workbook, as today), or a subscription for the seller's own tax team with the
firm as reviewer of record.
