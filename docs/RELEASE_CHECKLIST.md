# Cloud release checklist (must pass before customers use it)

- [ ] Gateway copied from the **tested, patched** local source checkout; .env excluded.
- [ ] New private GitHub repo contains no Excel, API key, auth keys, .sqlite3, WhatsApp session files, or database dumps.
- [ ] Docker Build succeeds on the chosen host CPU architecture (ARM64 vs x86_64).
- [ ] Strong, distinct session/password/DB/encryption secrets configured; HTTPS works on both domains.
- [ ] Gateway web admin protected and registration first-user bootstrap secured.
- [ ] Studio login, session cookie, CSRF and logout tested from the actual deployment URL.
- [ ] SQLite and MySQL have persistent volumes; backups and restore tested.
- [ ] Desktop 317 records and generated HTML migrated and checksum/preview checked if transferring them.
- [ ] Local WP Script Bot sender and cloud sender never operate the same account simultaneously.
- [ ] Gateway session ID is correct and runtimeStatus CONNECTED on cloud.
- [ ] Consent evidence recorded for every scheduled recipient; opt-out works.
- [ ] One recipient test: scheduled request accepted, recipient received document+caption.
- [ ] Second consecutive batch without manual refresh/reconnect.
- [ ] Temporary disconnect and server reboot: due jobs recover with no duplicate send.
- [ ] An ambiguous send goes to review, not automatic resend.
- [ ] Editing/deleting a pending schedule does not affect sent jobs.
- [ ] Security issues identified in WA-AKG repo remediated; dependency audit and access tests pass.
- [ ] Monitoring, backups, error alerts and recoverability verified before wide deployment.
