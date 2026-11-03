# Connecting a test bench to the platform

The platform **receives** finished engine reports. It never starts a wipe, never reaches into a laptop,
and never replaces the engine's own safety checks (live USB, OS disk, removable disk, serial check,
Asset/Job ID cross-check, dry-run unless authorised, verified sanitisation).

## One-time setup (in the web app)
1. Sign in as a company administrator. Open **Settings**.
2. **Stations > New station**. Copy the token shown (it is shown once).
3. **Trusted signing keys > Add key**. Paste `itad_signing_public.pem` from your ITAD_DATA stick.
   (Or push a report once, then press **Trust this key** in the Engine inbox.)

## Each job (at the bench)
```
export ITAD_PLATFORM_URL=https://your-platform
export ITAD_PLATFORM_TOKEN=...station token...
python3 website/integrations/itad_push.py --check AST-001 --serial SN123 --job JOB-0001   # optional, advice only
python3 website/integrations/itad_push.py /path/to/signed_report.json
```
You can also upload the same `.json` file in the web app: **Engine inbox > Upload a report file**.

## What the platform does with a report
- Re-computes the SHA-256 fingerprint and checks the Ed25519 signature.
- Keeps the report as evidence, even if rejected.
- Creates a *verified* erasure record only from an intact report.
- Re-calculates PASS / FAIL / INCOMPLETE / BLOCKED itself. It never trusts the report's own overall.
- Issues a certificate only for a verified erasure from a signed report whose key your company trusts.
