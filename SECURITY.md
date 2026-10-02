# Security Policy

## Supported versions

The current `main` branch is the only supported line. MoxMate has no
published releases or versioned support tiers.

## Reporting a vulnerability

Do **not** file public issues for undisclosed vulnerabilities.

Report security issues by opening a private report in the GitHub Security
Advisories for this repository (Security tab → **Report a vulnerability**),
or contact the maintainer at `alex.olvin@gmail.com`.

Please include:
- reproduction steps,
- the affected components,
- any relevant configuration, with secrets redacted.

## Authentication model (for reference)

Access to the web UI/API is gated by HTTP Basic with a single shared secret
(`SERVER_AUTH_SECRET`). Mutating operations are additionally protected by a
per-request CSRF token. When no secret is configured, the interface is
read-only and mutating operations fail closed with `403`.
