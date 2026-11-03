# Development roadmap

## Done in this version
Homepage; registration, sign-in, reset, e-mail verification, roles; dashboard with charts, filters and search; jobs; laptop assets with the 6-step workflow; engine report import with signature checks; calculated results; signed, lockable, revocable certificates with a public verification page; 7 report types in PDF and CSV with history; audit log; station tokens and trusted keys; Docker files; tests.

## Next (suggested order)
1. **Run on PostgreSQL** and fix anything the tests find (highest priority, never run here).
2. **Real mail** (SMTP) and a production e-mail template check.
3. **Real bench trial:** push a real signed report from a test laptop; compare every field.
4. **Two-factor sign-in** for administrators.
5. **Front end upgrade** to React/Next.js if wanted (API stays the same).
6. **Phone and network-switch modules** (the platform is built for laptops first).
7. **Customer extras:** certificate e-mailing, bulk CSV import of expected assets.
8. **Hardening review** by someone independent: penetration test, backup/restore drill, retention policy.
9. **Monitoring:** health alerts, log shipping.
