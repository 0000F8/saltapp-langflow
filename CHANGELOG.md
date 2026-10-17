# Changelog

## Unreleased

Send Message sends the send guard (`recipient_fingerprints`, `sender_fingerprint`) and, when Salt refuses with `stale_recipient_keys`, re-encrypts once to the current keys and resends. Needs `saltapp>=0.10.0`.

## 0.1.1

First PyPI release (`pip install saltapp-langflow`). The `saltapp` dependency is now
`saltapp>=0.6.0` from PyPI instead of a `git+https://...@main` pin; no component code changed.
