# Design note

The batch pipeline calls `mid.py::transform` for shaping, which depends on
`core.py::target` for the base computation.

```tunic-claims
[{"rel": "depends_on", "subject": "mid.py::transform", "object": "core.py::target"}]
```
