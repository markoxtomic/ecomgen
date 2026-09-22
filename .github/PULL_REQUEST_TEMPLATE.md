# What and why

<!-- What broke or was missing, and why this is the right fix. Link the issue if there is one. -->

## Evidence

<!--
For a fix, name the regression test and confirm it failed against the unfixed code.
A test that never failed first does not prove the bug is gone.
-->

- Regression test:
- Failed before the fix:

## Checks

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m ruff format --check .`
- [ ] CLI assertions (if any) use `PLAIN_CONSOLE` / `plain()` from `tests/conftest.py`

## Generated output

- [ ] No change to the bytes a fixed seed produces
- [ ] Output changes — described below, and `examples/sample_output` regenerated and re-validated

<!-- If output changed: which tables and columns, and why the new values are the correct ones. -->

## Invariants

Confirm the change keeps these, or explain why it deliberately changes one
(see [CONTRIBUTING.md](../CONTRIBUTING.md)):

- [ ] Determinism: same seed, same bytes, on every platform
- [ ] Gross VAT-inclusive accounting: `total = subtotal - discount + shipping`, `tax` contained in `total`
- [ ] Manifest stays authoritative (row counts, digests, no timestamps)
- [ ] Export stays atomic: a failure or Ctrl+C leaves the destination untouched
- [ ] `validate` still separates errors (exit 1) from warnings (exit 0)
- [ ] Docs updated if behaviour the README describes changed
