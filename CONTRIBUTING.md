# Contributing

1. Create a focused branch.
2. Add or update tests for semantic changes.
3. Run:

```bash
python -m compileall -q src tests packaging
python -m pytest -q
ruff check .
```

Keep the raw Freeplane model, semantic compiler, shell execution logic, and tmuxp emitter separated. New behavior should be expressed as a typed execution-plan change and covered by a map-level test.
