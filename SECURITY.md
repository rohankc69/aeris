# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private vulnerability
reporting ("Report a vulnerability" under the Security tab of the repository). You will get an
acknowledgement within a few days.

## Scope

AERIS is simulation-first research software. Reports we care about most:

- Anything that lets a component bypass the Safety Governor
- Anything that lets AI output reach a fleet adapter without validation
- Credential leakage (`TYPESAFE_API_KEY`, `DATABASE_URL`) in logs, records, or the API
- Authentication or authorization gaps in the REST/WebSocket API
- Unsafe deserialization of scenario files or external inputs

## Supported versions

Only `main` is supported until a first tagged release.

## Out of scope

Requests to add offensive, targeting, or weapons capability are not security reports and will
be closed. See the project scope in `README.md`.
