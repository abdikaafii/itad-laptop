# Competitor Research

Researched on 3 November 2026 from the vendors' public web pages and NIST's public FAQ.
This is desk research only: no trial accounts were opened, so anything not stated on a
public page is marked "not stated". Nothing here is copied; all points are paraphrased.

## 1. What was compared

| Platform | Type | Pages used |
|---|---|---|
| Ziperase (Command Center, Verify) | Network/USB erasure plus central reporting | ziperase.com/command-center, Ziperase Verify launch news |
| Blancco (Blancco Blue on-premise, Drive Eraser) | Erasure plus central management and reporting | blancco.com/products/management-console |
| Certus Software (Certus Erasure) | Erasure plus signed reports | certus.software erasure-reports page |
| NIST SP 800-88 Rev. 2 | The standard these tools are measured against | csrc.nist.gov SP 800-88 Rev. 2 FAQ |

## 2. Findings per platform

### Ziperase
- **Operations:** Command Center lets one operator erase laptops, desktops and servers in bulk
  over an isolated PXE network, with no monitor or keyboard attached. Devices without PXE can
  boot from USB or optical media.
- **Certificates:** every device gets a tamper-proof PDF certificate recording the method,
  verification outcome and system and storage details. Standards named: NIST SP 800-88 R2 and
  IEEE 2883:2022.
- **Reporting:** a central database searchable by serial number, "extra fields" for client info and
  notes, and export as PDF, CSV or JSON. Cloud-synced customer portal with multi-site view,
  filtering, licence usage and custom date-based reports.
- **Integrations:** REST API, read-only database access, webhooks on report upload, plus named
  ERP connectors.
- **Verify:** a separate product that independently re-checks that erasure really happened,
  aimed at R2v3, NAID and ADISA audits. The point it makes is that the party who erased the
  data should not be the only one vouching for it.
- **Gaps (from the public pages):** no dedicated asset-lifecycle module, and user roles and
  permissions are not described.

### Blancco (on-premise management platform)
- **Reporting:** device details in reports (name, model, IMEI, capacity) exported as PDF, XML or
  CSV, with a central dashboard across products.
- **Audit trail:** erasure activity logged with digitally signed certificates, described as
  tamper-resistant.
- **Users and licences:** central user management for global teams and licence distribution to
  sites. Specific roles are not described.
- **Deployment:** runs fully on local infrastructure. Also advertises a sustainability dashboard
  and compliance mapping (PCI DSS, HIPAA, GDPR).

### Certus Software
- **Report family:** a pre-erasure hardware audit report, a per-device certified erasure report,
  a batch erasure report covering successes and failures, and a verification report after an
  independent check.
- **Tamper protection:** each report carries a unique digital signature, and opening the report
  validates whether it was changed. Only original reports carry the signature.
- **Custom fields:** up to five custom fields on the audit report.
- **Gaps:** storage and export formats are not described on the page reviewed.

### NIST SP 800-88 Rev. 2 (what the certificate has to stand on)
- **Clear:** one logical overwrite is normally enough. Multi-pass overwrite is no longer treated
  as necessary.
- **Purge:** cryptographic erase counts only when strict conditions are met (validated crypto, no
  earlier plaintext on the media, key destruction).
- **Two-step assurance:** *verification* checks the technique finished without errors;
  *validation* is a management decision that the result is acceptable for the data's sensitivity.
- **Records:** policy should require evidence such as certificates of sanitisation.

## 3. Comparison against what the brief asks for

| Capability | Ziperase | Blancco | Certus | This platform |
|---|---|---|---|---|
| Per-device PDF certificate | Yes | Yes (signed) | Yes (signed) | **Yes**, with QR and public verification page |
| Tamper protection on certificates | Yes | Yes | Yes | **Yes**: content hash + server-side HMAC, database triggers block edits, plus the engine's own Ed25519 report signature is verified before anything is issued |
| Central searchable asset database | Yes | Yes | Not stated | **Yes** |
| Customer portal | Cloud portal | Not stated | Not stated | **Yes**, strictly isolated per customer |
| Role-based access | Not stated | Not stated | Not stated | **Yes**, four roles |
| Hardware test results beside erasure | Partial | Not stated | Hardware audit report | **Yes**, one record per laptop |
| Independent verification step | Verify (separate product) | Not stated | Verification tool | **Built in**: certificates refuse to issue unless the report signature checks out against a registered station key |
| Export formats | PDF, CSV, JSON | PDF, XML, CSV | Not stated | PDF, CSV, JSON |

## 4. Where we can be better

1. **Simplicity.** One screen per laptop that shows identity, erasure evidence, tests and
   certificate together. Competitors split this across products.
2. **Verification built in, not bolted on.** Certificates are only issued from reports whose
   Ed25519 signature validates against a key the company registered. An unsigned or unknown-key
   report is stored but cannot produce a certificate.
3. **Honest status.** The engine's INCOMPLETE and WARN states are kept as they are. Nothing is
   rounded up to PASS.
4. **Management validation step.** Following NIST's verification/validation split, issuing a
   certificate is an explicit action by a named person (automatic issue is an optional setting,
   off by default).
5. **Laptop depth.** Absolute, Autopilot/MDM enrolment, wipe method per drive, hidden-area
   handling and 15 component tests are first-class fields, not free text.
6. **Customer clarity.** Customers see only their own assets, jobs and certificates, and can
   verify any certificate on a public page without logging in.

## 5. What we deliberately do not copy

Branding, layouts, wording and proprietary formats are all original. The only things shared with
the competitors are standards (NIST SP 800-88, IEEE 2883) and common-sense features such as PDF
certificates and CSV export.

## 6. Limits of this research

- Public marketing pages only; feature depth behind logins was not seen.
- Certification claims (for example ADISA or Common Criteria levels) are the vendors' own and
  were not independently checked.
- Pricing was not researched.

## Sources

- [Ziperase Command Center](https://ziperase.com/command-center)
- [Ziperase Verify launch](https://ziperase.com/news/ziperase-launches-verify-independent-verification-for-r2v3-naid-compliance/)
- [Blancco management platform](https://www.blancco.com/products/management-console/)
- [Certus erasure reports](https://certus.software/en/about-the-software/erasure-reports)
- [NIST SP 800-88 Rev. 2 FAQ](https://csrc.nist.gov/files/pubs/sp/800/88/r2/final/docs/sp800-88r2-faq.pdf)
