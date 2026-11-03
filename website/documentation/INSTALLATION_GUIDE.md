# Installation guide (simple version)

Everything below happens on the computer or server that will run the website, in a terminal, inside the `website` folder of the repository.

## A. Try it on your own computer (no Docker)
1. Install Python 3.10 or newer.
2. In the `website/backend` folder run: `pip install -r requirements.txt`
   (psycopg2 is only needed for PostgreSQL; if it fails to install on your machine you can remove that line for this trial).
3. Still in `website/backend`, start it: `ITAD_ENV=development python3 -m uvicorn app.main:app --port 8000`
4. Open http://localhost:8000 in a browser. The app is at http://localhost:8000/app.
5. Demonstration logins (development only, password `Demo-Passw0rd!`, change with `ITAD_DEMO_PASSWORD`):

| E-mail | Role |
|---|---|
| super@demo.itad | Super administrator |
| admin@demo.itad | Company administrator |
| tech@demo.itad | Technician |
| customer@demo.itad | Customer |

   Demo data is labelled DEMONSTRATION and can never produce a valid certificate. **Never use these accounts in production.** In production demo data is switched off.

## B. Run with Docker and PostgreSQL (production style)
1. In the `website` folder copy `.env.example` to `.env` and change every value marked CHANGE (secret key, database password, first admin e-mail and password, your public address).
2. Run: `docker compose -f docker/docker-compose.yml --env-file .env up --build -d`
3. Open the address you set. Sign in with the first administrator, create your company, then add users.
4. Put it behind HTTPS (for example a reverse proxy such as Caddy or nginx). Cookies are Secure in production, so HTTPS is required.
5. Back up the `dbdata` and `appdata` volumes. `appdata` holds the certificate signing key. **Losing it means old certificates can no longer be shown as valid.**

## B2. Temporary public demo on Render (free, demo data only)
Everything here is on the Render website, plus one GitHub step.
1. Sign up at render.com with your GitHub account.
2. Click **New > Blueprint**, choose the repo `abdikaafii/itad-laptop` and the branch `website-platform`. Render reads `render.yaml` in the repo root.
3. Click **Apply**. The first build takes a few minutes.
4. When it says Live, open the link. Sign in at `/app` with `admin@demo.itad`.
5. The demo password is random here: Render dashboard > your service > **Environment** > `ITAD_DEMO_PASSWORD`.
6. Limits: it sleeps after about 15 minutes idle (the next visit takes about a minute), and the free plan forgets its data on each restart, so demo data is re-created. Never put real customer data on it.
Not tested on Render itself (no Docker available on the build machine); the start command was tested locally.

## C. Connect a test bench
See `website/integrations/README.md`.

## D. Run the automated tests
In the `website` folder: `python3 -m pytest tests -q` (44 tests: sign-in, access rules, customer separation, engine import, tampering, certificates, every report type, public verification).

## Troubleshooting
- "ITAD_SECRET_KEY must be set in production": add it to `.env`.
- Cannot sign in after registering: open the e-mail link first (in development the link is shown on screen).
- Port busy: change `--port 8000` or the Docker port mapping.
