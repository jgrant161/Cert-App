# Build-out plan

Updated October 12, 2026. This follows the phases in Steven's review of the demo. Phases 1 and 2
come first, because reminders and exposure analysis are only as good as the certificate data
underneath them.

**Status key:** **Done** = in the app now · **Partly** = a first version is in, more to build ·
**Planned** = not started.

## Already in place (before Steven's review)

| Item | Status |
|---|---|
| AI reading of certificate PDFs, batch upload, duplicate detection, reconciliation | Done |
| Review policy toggles, reviewer overrides, follow-up list, Excel deliverable | Done |
| Plain-English questions about a client's certificates | Done |
| Sign-in with four roles, client separation, activity log | Done |
| Microsoft 365 sign-in for staff, restricted to @insighttaxadvisors.com | Done (switches on once hosted) |
| PostgreSQL, container and Azure guide for cloud hosting | Done; Azure setup is with IT |
| Vendor certificate portal (prospect request) | Prototype |

## Phase 1: Current-state fixes (done)

| Steven's item | Status | What was built |
|---|---|---|
| Validity fields on the Schedule: date signed, expiration, exemption reason, signature, purchaser address, seller name match | Done | Schedule shows signed and date, expiration, and status; the Excel Schedule adds exemption reason, purchaser address, seller on the certificate and whether it matches the engagement's seller. Missing address and wrong seller are flagged. |
| Status column (Valid / Invalid / Expired / Pending); expired off the schedule or clearly marked | Done | Status on every row and certificate, with the reason. Expired certificates stay on the schedule marked Expired by default; one policy setting moves them to Excluded lines instead. |
| Expiration on the Schedule, not only on Follow-up | Done | Expires column on screen and in Excel, noting when the date comes from a state rule. |
| No expiration date: apply the state's rule | Done | Each engagement's review policy holds rules such as "IL: 3 years" or "FL: calendar year". **Our tax team supplies and confirms the rules**; the demo carries two sample rules. |
| Does each state accept the form (SST in a non-member state, Uniform where a state requires its own form) | Done | SST certificates are checked against the 23 SST member states plus Tennessee (associate), built in. States that refuse the MTC Uniform certificate are a policy list **our tax team keeps current**, because published lists disagree. |
| FEIN and home-state settings state by state | Done | Each setting keeps its overall default plus an "except in these states" list. |
| Different rules per exemption type | Partly | Government, nonprofit, manufacturing, agricultural and direct-pay certificates skip the resale registration-number test; a missing exemption reason is flagged. Still to come: state-specific requirements by exemption type (for example, which states need an exemption number on a nonprofit certificate). |
| Importer sorts out the file on the first pass: split bundled certificates, flag non-certificate pages | Done | One row per certificate, tied to its pages; cover letters, W-9s and similar pages are listed and left off. Re-reading any part re-reads the whole file. Needs an AI read (demo mode reads filenames only). |
| Confidence score on each extracted field | Done | Confidence for form, purchaser, seller, state lines, signature and expiration; medium and low are marked beside the field, and low ones are flagged for the reviewer. |
| Merge customer name variants (Inc vs no Inc, parent vs subsidiary) | Done | Spelling variants join automatically; a Customers tab lets reviewers join parents, subsidiaries and trade names, and undo it. |
| Status, flags and reviewer notes in the Excel export | Done | On every Schedule row and in Reconciliation, plus a Customers tab and status counts on the Overview. |

## Phase 2: Database and audit trail (next)

| Item | Status | Notes |
|---|---|---|
| Customer master: one record per customer, the client's ERP customer ID, contact info | Planned | Builds on the Customers tab from Phase 1. |
| Every certificate searchable and sortable (customer, state, status, expiration, exemption type), download at any level | Partly | Search by customer, file or number, and filter by state and status, work today. Sorting, an exemption-type filter and bulk download are to come. |
| Activity log per certificate: received, reviewed, overridden, requested, replaced, with who and when | Partly | A firm-wide activity log already records uploads, reviews, overrides and downloads; a per-certificate history view is to come. |
| Audit package export: schedule plus certificate PDFs for an audit sample in one download | Planned | |
| Follow-up fields: owner, date requested, due date, client response, resolution, reviewer sign-off | Partly | Review status, reviewer note and a reviewer status decision exist; the dated follow-up workflow is to come. |

## Phase 3: Exposure analysis

| Item | Status |
|---|---|
| Import exempt sales by customer and ship-to state (ERP extract or a standard template) | Planned |
| Match each sale to a valid certificate for that customer, state and date | Planned |
| Report uncovered dollars and estimated tax at risk by state, customer and period | Planned |
| Reusable import template and customer-matching rules for fast onboarding | Planned |
| Package for audit defense, VDAs, M&A diligence and recurring health checks | Planned |

## Phase 4: Automated reminders and collection

| Item | Status | Notes |
|---|---|---|
| Automatic requests for missing certificates, driven by the exposure analysis | Planned | Needs Phase 3. |
| Renewal reminders before expiration (for example 90, 60 and 30 days) | Partly | Expiring-soon flags exist (window set per engagement); sending reminders needs email from the app. |
| Customer upload link so returned certificates go straight into the importer | Planned | The vendor portal's sign-in and upload pieces can be reused. |
| Track each request: sent, opened, received, accepted or rejected, with escalation | Planned | |
| Client-approved email templates sent under the client's name, on/off per client | Planned | |

## Phase 5: AI throughout

| Item | Status | Notes |
|---|---|---|
| Plain-English questions ("Which TX customers have no valid certificate?") | Partly | Works per client today, now including status and expiration. Questions across clients and over sales data come with Phases 2 and 3. |
| Explain every decision: why a certificate was accepted, rejected or flagged | Partly | Every state line shows why it was or was not scheduled, and every status shows its reason. A written explanation per certificate is to come. |
| Draft follow-up emails, client summaries and audit response memos | Planned | |
| Learn from reviewer overrides so reads improve on each form type | Planned | Start with a labelled test set from the AEI and Additive Manufacturing engagements and measure every change against it. |

## Running alongside the phases

| Item | Status |
|---|---|
| Azure hosting so the remote team and prospects can reach the app (brief prepared for IT) | In progress |
| Retention rules, a tested restore, and a SOC 2 path when larger clients ask | Planned (access controls and client separation are done) |
| Client portal polish: two-step sign-in for client password accounts, invitation emails from the app | Planned |
| Lower cost for large backlogs through the Message Batches API | Planned |

## Positioning and pricing (from Steven's notes)

- Avalara, Vertex and Sovos already own ongoing certificate management. Our edge is the review
  judgment and the exposure analysis, so Phases 1–3 are what we sell first.
- Pricing: per engagement for batch reviews, and a subscription for ongoing management (see the
  pricing proposal).
- We will hold customer and sales data, so access controls, client separation and retention
  rules must be in place from day one. The first two are built; retention rules are on the list above.
