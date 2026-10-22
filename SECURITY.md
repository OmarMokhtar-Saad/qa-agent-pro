# Security policy

## Supported versions

Only the **latest release** receives fixes. Installs update themselves, so
running `qa-doctor` in your editor brings you to the newest version.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's **private vulnerability
reporting**: on this repository open the **Security** tab and choose **Report a
vulnerability**. Include the version (`qa-doctor` prints it), your OS, and the
steps to reproduce.

You will get an acknowledgement, and a fix ships as a new release once the
problem is confirmed.

## Integrity

Releases carry a `MANIFEST.sha256` of every code file, and the launcher verifies
it on each start. The server calls no model itself and sends no test content
anywhere; see [docs/telemetry.md](docs/telemetry.md) for what analytics are sent
and how to turn them off.
