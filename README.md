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
| **AI reading** | Claude reads each PDF and reports what is on the page: form, purchaser and address, seller, certificate type, exemption reason, signature and date, expiry, and **every state line on multistate grids** with the exact text and a classification (registration number, home-state number, FEIN, N/A, blank, dashed, "See attached", other text). It rates its confidence in each part, so reviewers know where to look. |
| **Bundled files** | A PDF holding several certificates is split: each certificate gets its own row tied to its pages, and pages that are not certificates (cover letters, W-9s) are listed and left off the schedule. |
| **Validity status** | Every certificate and schedule row is **Valid**, **Invalid** (unsigned, made out to another seller, no purchaser, a form the state does not accept, no qualifying state), **Expired** (the stated date, or the state's rule when no date is stated), or **Pending** (not read yet, or waiting on a reviewer). Hover a status for the reason. |
| **Review policy** | The judgment calls (does "N/A" count? a home-state number? an FEIN on every line? a number followed by "exempt per nexus rules"?) are per-client toggles, with state-by-state exceptions for home-state numbers and FEINs. The policy also holds the states that refuse the MTC Uniform certificate, each state's expiration rule, and whether expired certificates stay on the schedule. Change one and the schedule re-scores instantly. Nothing is re-read. Streamlined (SST) certificates are checked against the SST member list built into the app. |
| **Exemption types** | Government, nonprofit, manufacturing, agricultural and direct-pay certificates are not held to the resale registration-number test; a missing exemption reason is flagged instead. |
| **Customers** | Name variants ("Acme Supply, Inc." / "ACME Supply") are joined into one customer automatically; reviewers join parents, subsidiaries and trade names by hand. |
| **Flags** | Wrong state in the filename, multijurisdiction files that are really single-state, TBD files resolved, no qualifying state, the same number repeated across states, nexus disclaimers, expired or expiring certificates, unsigned forms, entity-based exemptions, exact and content duplicates, low-confidence reads, and anything else the reader noticed (for example extra certificates bundled on later pages). |
| **Reviewer workspace** | Each certificate opens side by side with its PDF, showing every state line, whether it made the schedule and why, its status per state, and one-click include/exclude overrides. Marking it Reviewed clears Pending; a reviewer can also set the status by hand with a note. Review status and notes carry through to the export. |
| **Excel export** | Overview (including the policy used and status counts), Schedule (status, validity fields, flags, review status and reviewer notes on every row), Summary by State, Summary by Customer, Customers, Follow-Up, Excluded Lines, Reconciliation (every certificate and the rows it produced, with totals), and Batches. |
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
| `CERTAPP_FIRM_DOMAINS` | `insighttaxadvisors.com` | Email domains allowed for firm accounts |
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
| **Vendor** | Only their own certificate requests, in one client's vendor portal | Ask whether a sale is exempt and download the certificate the portal hands out |

- **Firm accounts must use an @insighttaxadvisors.com address.** Firm administrator and firm
  staff accounts with any other domain cannot be created, cannot be promoted to, and cannot sign
  in (this also blocks Microsoft 365 guest accounts from other companies). Client accounts can use
  any address. Change or extend the list with `CERTAPP_FIRM_DOMAINS` (comma-separated).
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

## Vendor certificate portal (prototype)

A client's vendors sign in, choose the project they are selling to (grouped by buying entity),
list their items with a category, and get one of three answers per item:

- **Exempt**: the matching signed certificate is ready to download.
- **Taxable**: charge sales tax.
- **Needs review**: contact the client's tax team (shown on the page, with the request number).

The firm sets up each client's portal from the client page (**Vendor portal**): buying entities,
projects (state and type), item categories, rules, the certificate library (signed PDFs) and the
contact. Rules decide everything; nothing is guessed. The most specific rule wins (a rule for the
project's state beats "any state", then a rule for the project type beats "any type"). An item no
rule covers, or an exemption with no current certificate on file, goes to the contact. Every
request and every certificate download is logged. Vendors are added on the **Users** page with
the Vendor role and see only their own requests. `tests/test_portal.py` checks the rules and the
separation.

Try it with sample data (made-up rules, not tax advice):

```
python scripts/seed_portal_demo.py
python -m certapp serve
```

Sign in as `vendor@acme-supply.test` (vendor) or `demo.admin@insighttaxadvisors.com` (firm),
password `demo-password-1`. Try the Houston project with "Manufacturing machinery and equipment"
(exempt, certificate) and the Tampa project with "Construction materials" (contact).

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

The build-out plan, with what is done and what comes next, is in
[docs/ROADMAP.md](docs/ROADMAP.md).
