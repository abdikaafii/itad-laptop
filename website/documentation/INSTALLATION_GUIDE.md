# Installation guide

Where each step happens is marked **[Laptop]** (your own Windows/Linux computer or any server).

## A. Try it on your own computer (development)

1. **[Laptop]** Install Python 3.10+.
2. Open a terminal in the `website/backend` folder and run:
   ```
   pip install -r requirements.txt
   set ITAD_DEV_OUTBOX=1            (Windows;  Linux/Mac: export ITAD_DEV_OUTBOX=1)
   python manage.py init-db
   python manage.py seed-demo
   python manage.py run
   ```
3. Open http://127.0.0.1:5000 . Demo logins are written to `backend/instance/demo_credentials.txt`
   (random passwords; everything is labelled DEMONSTRATION).
4. Emails (verify, reset) appear at http://127.0.0.1:5000/dev/outbox in this mode only.
5. Tests: from `website/` run `python -m pytest tests`.

## B. Real server (about €5/month VPS)

1. Get an Ubuntu VPS and a domain name pointing at it.
2. Copy `website/` to the server. Copy `.env.example` to `.env`; set `APP_ENV=production`,
   `ITAD_SECRET_KEY` (see the comment in the file), `ITAD_PUBLIC_URL`, SMTP details, `ITAD_TRUST_PROXY=1`.
3. With Docker: `docker compose -f docker/docker-compose.yml up -d --build`
   Without Docker: `pip install -r backend/requirements.txt` then in `backend/`
   `gunicorn --workers 2 --bind 127.0.0.1:8000 wsgi:app`.
4. HTTPS: install Caddy and use `docker/Caddyfile.example` (certificates are automatic).
5. Create the first owner: `python manage.py create-superadmin you@example.com`
   (in Docker: `docker compose -f docker/docker-compose.yml exec itad python manage.py create-superadmin you@example.com`).
6. Do **not** run `seed-demo` on the real server.
7. Back up the `/data` folder (database and uploads) and your `ITAD_SECRET_KEY`. Losing the secret key makes old certificates fail verification.

## C. Connect the boot-USB engine

1. In the site: **Company & keys → API tokens → Create token**; copy it.
2. **Station keys**: register the stick's `itad_signing_public.pem` (or confirm from the pending list after its first upload).
3. After a laptop is processed, upload its final report:
   `python integrations/upload_report.py --server https://your-site --token itad_xxx report.json`
4. In the site open the laptop, check the result, then **Issue certificate**.

The existing engine files were not changed.
