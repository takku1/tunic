# Gateway migration — 2026-02-03

Session: migration. Author: operations.

Effective 2026-02-01, Aster sends invoices through the Birch gateway,
replacing Cedar. Birch uses /invoice/v2. Nolan owns Birch.
The migration leaves the retry policy and launch region unchanged.
Cedar remains available for archive exports, not Aster invoice requests.
