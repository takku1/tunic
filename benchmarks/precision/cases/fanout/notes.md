# Store notes

Please read the runbook before you update the audit log. The main workflow
is: read the incoming cart, update the record, and audit the result.

The checkout path is `service.py::checkout`, which calls
`core.py::calculate_total` for totals.

Remember to read the main audit checklist and update it after every change.
The team reads these notes to audit every update to the main process.
