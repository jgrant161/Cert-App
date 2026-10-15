# Where to host the consignment portal

Consignors sign in from home, so the portal has to live on a server on the internet with HTTPS, not on the shop computer. Below are the realistic choices, as of October 2026. Prices change; check each company's pricing page when you sign up.

## What the portal needs

- One small always-on web server (it is light: a few people at a time).
- A **persistent disk** for the database and the item photos, so nothing is lost when the server restarts or updates.
- HTTPS (the padlock) on your own address, such as `consign.dugouttx.com`.
- Backups.
- Somewhere safe to keep the Square and eBay keys.

## The options

| Host | About per month | Ease for a beginner | Security and backups | Fit |
|---|---|---|---|---|
| **Render** (recommended) | **~$8.25**: Starter server $7 + 5 GB disk $1.25 | **Easiest.** Sign in with GitHub, click *New → Blueprint*, pick the repository; the included `render.yaml` sets everything up. Updates deploy by themselves when the code changes. | Free automatic HTTPS, including your own domain. Keys are stored in an encrypted *Environment* page. The disk is **snapshotted daily, kept at least 7 days**, and can be rolled back from the dashboard. | Everything the portal needs, nothing extra. |
| Railway | ~$5–15 (a $5 plan that includes $5 of usage, then metered by the second) | Easy, GitHub-based, but you set up the disk ("volume") and settings by hand. | HTTPS included. Volume backups exist but need setting up. | Works; the bill varies month to month. |
| DigitalOcean App Platform | ~$25+ ($5 app + $15 managed database + photo storage) | Moderate. | Good, with managed database backups. | App Platform has **no persistent disk**, so photos would need separate storage and code changes. Not a good fit today. |
| Fly.io | ~$3–5 with a volume; ~$40+ with their managed database | **Hard**: set up from a command line. | Good, but you configure more yourself. | Fine for developers; not for a first deployment. |
| PythonAnywhere | $10 | Easy for some Python apps. | Fine. | Its support for this kind of app (FastAPI/ASGI) is still experimental. Not recommended. |
| Your own rented server (VPS) such as a $4–6 DigitalOcean or Hetzner server | $4–6 | **Hardest.** You install and update Linux, the web server and HTTPS yourself. | Only as good as your upkeep: missed security updates are the main risk. | Cheapest, but not worth it for a beginner. |
| The shop computer | $0 | Easy to start. | Opening it to the internet is risky; it's offline whenever the computer is; one disk failure loses everything. | Only for trying it out. |

### Recommendation: Render, Starter plan, with a 5 GB disk

- **Cost:** about **$8.25 a month** ($7 server + 5 GB × $0.25). 5 GB holds tens of thousands of phone photos; you can make it bigger later in two clicks, but not smaller. The domain is the one you already own at GoDaddy, so no extra cost there. Square's and eBay's APIs are free.
- **One thing I couldn't confirm:** in 2026 Render changed how it charges for *workspaces* (the account the services sit in). One report says it moved to a flat $25/month Pro fee for teams. A single-person account has traditionally been free ("Hobby"). When you sign up, choose the free/Hobby workspace if it's offered. If Render insists on a paid workspace, stop and check render.com/pricing, or ask me to compare Railway again.
- **Why not the free plan?** Render's free servers go to sleep after 15 minutes without visitors (the next visitor waits about a minute) and can't have a disk, so photos and the database would be wiped on every restart.
- **Database:** the portal keeps its data in a single file on the disk (SQLite), which comfortably handles a shop's volume and keeps everything (data and photos) in the one daily snapshot. If the shop grows a lot, it can switch to a managed PostgreSQL database by setting one value (`DATABASE_URL`); the portal supports both.

## Security, in plain terms

What the portal already does:

- Passwords are stored scrambled (scrypt), never in plain text. Five wrong passwords in a row lock that email out for 15 minutes.
- In production it only works over HTTPS, its sign-in cookie can't be read by scripts or sent over plain HTTP, and it refuses form posts that come from other websites.
- The first owner account can only be created with a one-time **setup code** that only you can see in Render.
- Consignors can only ever see their own items, sales, statement and payouts. Shop pages don't even admit they exist to a consignor (tests check every one).
- Staff can do daily work; only the owner can change settings, add staff, connect eBay, or void sales and payouts. Every change is logged with who did it and when (Settings → Activity log).
- Square and eBay keys live only in Render's Environment page, never in the code or on GitHub.

What you should do (Part 7 of the setup guide walks through it):

1. Turn on **two-step verification** on GitHub, Render, Square, eBay, GoDaddy and the email account they all use. This matters more than anything else on this list.
2. Use a long, unique owner password (a password manager helps).
3. Never paste a Square token or eBay secret into email, text or chat. If one leaks, replace it (the guide shows where) and update Render.
4. Turn off staff and consignor logins when people leave.
5. Once a month, Settings → **Download a backup** and save the .zip to OneDrive or Google Drive. Render's daily snapshots cover a week; your own copy covers everything else.
