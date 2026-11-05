# Deadline update — 2026-03-02

Session: performance. Author: reliability team.

Effective 2026-03-01, Aster's request deadline is 360 milliseconds,
replacing the launch deadline of 240 milliseconds.
There are still at most two retries and the same invoice_token reuse rule.
This larger deadline allows the invoice gateway to complete during load spikes.
