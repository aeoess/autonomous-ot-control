# Security

This repository is a **simulation testbed**. It must never be connected to a
real building, BMS, WebPort instance or other physical actuator. Physical
integration requires a separate SDD, hazard analysis, independent interlocks
and site authorization (SDD-OT-001 Gate E).

Report vulnerabilities privately via GitHub Security Advisories on this
repository. Do not open public issues for bypasses of the actuator boundary.

In scope: any path by which an agent, runner, Worker, Durable Object, UI or
federation participant can cause a simulated write without the protected
dispatcher credential; replay producing a second effect; token leakage into
images, logs or artifacts; evidence that reports PASS for a failed or skipped
case.

Never commit `.env`, tokens or real site data. `deploy/scripts/preflight.sh`
fails the build if a twin token value appears in tracked files.

How the testbed answers each risk in the OWASP Top 10 for Agentic Applications
(ASI01–ASI10), what tests it, and what is not covered:
[docs/security/owasp-agentic-top10.md](docs/security/owasp-agentic-top10.md).
