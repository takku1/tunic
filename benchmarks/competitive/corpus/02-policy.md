# Retry decision — 2026-01-12

Session: reliability. Author: reliability team.

Aster retries failed invoice requests at most two times.
Invoice creation includes an idempotency key named invoice_token.
Retried requests must reuse the original invoice_token to prevent duplicate invoices.
The team considered five retries but rejected that option because of duplicate load.
