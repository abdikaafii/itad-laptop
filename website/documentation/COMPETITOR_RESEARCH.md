# Competitor research

Purpose: understand what established data-erasure and ITAD platforms offer, so ITAD Seal can be
useful in the same areas and clearly better in a few. Nothing here copies any vendor's branding,
text, layout or code. This is a summary of publicly described capabilities, read from the vendors'
own public pages (links below). Features can change; check the sources again before using this in marketing.

## Sources read
- ZipErase Command Center: https://ziperase.com/command-center
- ZipErase ITAD services: https://ziperase.com/itad-services
- Blancco Management Portal (product sheet, PDF, on blancco.com)
- Certus erasure reports: https://certus.software/erasure-reports
- Certus device grading and valuation: https://certus.software/device-grading-and-valuation

## What they offer (in plain words)
| Area | ZipErase | Blancco | Certus |
|---|---|---|---|
| Central web portal for results and users | Yes (Command Center) | Yes (Management Portal) | Yes |
| Erasure certificates / reports | Yes | Yes, with signed reports | Yes, tied to each erasure |
| Hardware / device testing | Part of its service offering | Separate diagnostics products | Included with grading |
| Device grading and valuation | Limited | Limited | A headline feature |
| Customer-facing access to their own assets | Yes | Via portal roles | Yes |
| Standards language (NIST 800-88 etc.) | Yes | Yes | Yes |

## What good looks like (patterns worth matching)
1. One place to see every device, job, result and certificate.
2. Role-based access with strict separation between customers.
3. Reports that can be exported and re-checked later.
4. An audit trail of who did what.
5. Clear wording about which standard and method was used.

## Gaps and opportunities (where ITAD Seal aims to be different)
1. **Evidence first.** A certificate can only come from an intact, signed engine report. A person cannot type in a "verified wipe". The platform re-calculates the result itself and ignores a report's own claim of PASS.
2. **Tamper-evidence you can check without us.** Reports use SHA-256 + Ed25519. Certificates are signed by the platform and can be verified on a public page without logging in (the page hides the full serial number).
3. **Plain honesty.** Demonstration data is labelled everywhere and can never produce a valid certificate. Anything not tested shows as NOT TESTED, never as passed.
4. **Safety stays at the bench.** The website never starts a wipe and never overrides the station's checks (live USB, OS disk, removable disk, serial, Asset/Job ID).
5. **Tamper-resistant history.** The audit log is hash-chained; certificates and erasure records are locked by database rules; revocation is a separate, visible record.
6. **Small-operator friendly.** Runs from one container, a free database option, simple wording for technicians.
7. **Europe/Dublin time stamps** on every record shown to users.

## Not claimed
No claim is made of certification, accreditation or independent approval. ITAD Seal is a working platform
built around an existing open engine; formal certification of methods or of the platform itself would be a separate step.
