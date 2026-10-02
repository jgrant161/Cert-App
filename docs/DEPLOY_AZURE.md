# Deploying to Microsoft Azure

This guide puts Certificate Review on the internet at your own address (for example
`https://certs.yourfirm.com`), with firm staff signing in through Microsoft 365.
It assumes the firm already has Microsoft 365, which means it already has a
Microsoft Entra ID (formerly Azure AD) directory and can create an Azure subscription
under the same organization.

Plan on an IT person or consultant for the first deployment. Each step is ordinary
Azure work; nothing here is unusual.

## What gets created

| Piece | Azure service | Holds |
|---|---|---|
| The app | **App Service** (Linux, container) | The program itself, built from this repository's `Dockerfile` |
| Database | **Azure Database for PostgreSQL – Flexible Server** | Clients, users, readings, overrides, the activity log |
| File storage | **Storage account → Azure Files share**, mounted into the app | The uploaded certificate PDFs |
| Container registry | **Azure Container Registry** | The built app image |
| Secrets | App Service settings (or **Key Vault** references) | API key, database password, signing key |
| Staff sign-in | **Microsoft Entra ID app registration** | Lets the app use your Microsoft 365 sign-in |

Put everything in one resource group in one region (for example East US 2), and use
the Azure pricing calculator to estimate monthly cost for your chosen sizes before
creating anything.

## 1. Register the app with Microsoft 365 (Entra ID)

1. In the Azure portal, open **Microsoft Entra ID → App registrations → New registration**.
2. Name: `Certificate Review`.
3. Supported account types: **Accounts in this organizational directory only (single tenant)**.
   This is what keeps sign-in limited to your firm.
4. Redirect URI: platform **Web**, value `https://certs.yourfirm.com/auth/microsoft/callback`
   (use your real address; while testing, the `*.azurewebsites.net` address works too, and you can add both).
5. After it is created, copy from **Overview**:
   - **Application (client) ID** → `CERTAPP_MS_CLIENT_ID`
   - **Directory (tenant) ID** → `CERTAPP_MS_TENANT_ID`
6. **Certificates & secrets → New client secret**. Copy the secret's **Value** (shown once)
   → `CERTAPP_MS_CLIENT_SECRET`. Note its expiry date and put a reminder on the calendar to
   renew it before then.
7. API permissions: the default **Microsoft Graph → User.Read (delegated)** is all the app needs.

Two-step sign-in (MFA) for staff is controlled by your Microsoft 365 security settings
(Security defaults or Conditional Access), not by this app. Turn it on there if it is not
already required.

## 2. Create the database

1. Create **Azure Database for PostgreSQL – Flexible Server** (PostgreSQL 16).
   A small burstable size is enough to start.
2. Create a database named `certapp` and a login for the app.
3. Networking: allow access from the App Service (public access limited to Azure services,
   or private networking if your IT team prefers).
4. Backups are automatic; set the retention period you want (for example 14–35 days).
5. Connection string for the app (keep `sslmode=require`):
   `postgresql://USER:PASSWORD@SERVER.postgres.database.azure.com:5432/certapp?sslmode=require`
   → `DATABASE_URL`

The app creates its tables on first start.

## 3. Create file storage for the PDFs

1. Create a **Storage account**, then a **File share** named `certfiles`.
2. Turn on soft delete and/or Azure Backup for the share so deleted files can be recovered.

## 4. Build the app image

From a computer with Docker, or from a GitHub Actions workflow:

```bash
az acr build --registry YOURREGISTRY --image certapp:1 .
```

Use a new tag (`certapp:2`, `certapp:3`, …) for each release so you can roll back.

## 5. Create the App Service

1. Create a **Web App**: publish **Container**, operating system **Linux**, image from your
   Container Registry. One instance is the right setting: certificate reading runs inside the
   app, so scale *up* (bigger instance) rather than *out* (more instances).
2. **Configuration → Path mappings**: mount the `certfiles` share at `/mnt/certfiles`.
3. **Configuration → Application settings** (see `.env.example` for the full list):

   | Setting | Value |
   |---|---|
   | `CERTAPP_ENV` | `production` |
   | `CERTAPP_SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `CERTAPP_BASE_URL` | `https://certs.yourfirm.com` |
   | `DATABASE_URL` | from step 2 |
   | `CERTAPP_FILES_DIR` | `/mnt/certfiles` |
   | `CERTAPP_SETUP_TOKEN` | a one-time code you choose (remove after setup) |
   | `ANTHROPIC_API_KEY` | from console.anthropic.com |
   | `CERTAPP_MS_TENANT_ID`, `CERTAPP_MS_CLIENT_ID`, `CERTAPP_MS_CLIENT_SECRET` | from step 1 |
   | `WEBSITES_PORT` | `8000` |

   Storing the secrets as **Key Vault references** instead of plain values is better practice.
4. **Settings → Health check**: path `/healthz`.
5. **TLS/SSL**: keep **HTTPS Only** on. Add your custom domain and a free App Service managed
   certificate.

## 6. First sign-in

1. Open `https://certs.yourfirm.com`. You are taken to **Create the first administrator**.
2. Enter the setup code from `CERTAPP_SETUP_TOKEN`, your name and work email, and choose
   **With Microsoft 365**.
3. Sign in with Microsoft. You are now the firm administrator.
4. Remove `CERTAPP_SETUP_TOKEN` from the app settings (the page closes itself after the first
   administrator anyway).
5. Under **Users**, add the rest of the staff (Microsoft 365 sign-in) and assign them to clients.

If you are ever locked out, run this from the App Service SSH console to get a new link or admin:

```bash
python -m certapp create-admin --email you@yourfirm.com --name "Your Name" --microsoft
python -m certapp sign-in-link --email someone@client.com
```

## Updating to a new version

1. Build a new image tag (step 4).
2. In the App Service, point the container at the new tag and save. The app restarts.
3. Certificates that were mid-read when it restarted are picked up again with **Resume reading**.

## Before client data goes on it

- Confirm backups for both the database and the file share, and test a restore once.
- Decide how long to keep client data after an engagement ends, and delete clients on schedule.
- Review who has Azure portal access; anyone with it can reach the data.
- Have the firm's attorney finish the client agreement and data-handling terms.
