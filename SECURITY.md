# Security Policy

## Reporting a vulnerability

Report it privately, not in a public issue: use
[Report a vulnerability](https://github.com/gr33ngiant112/hunter-bargain/security/advisories/new)
on the repository's Security tab (GitHub private vulnerability reporting).

Say what you found, how to reproduce it, and the version or commit you tested. Leave API keys,
passwords and other secrets out of the report.

This project has one maintainer, so replies are best effort.

## Supported versions

Fixes go to the `develop` branch and ship in the next release. Only the latest release is
supported.

## Known limitation

The API has no authentication of its own yet, so anyone who can reach its port can use it. Docker
Compose publishes it on 127.0.0.1 unless `HB_BIND_ADDR` says otherwise (README "Deployment").
Issue [#1](https://github.com/gr33ngiant112/hunter-bargain/issues/1) tracks this in public, so it
needs no private report.
