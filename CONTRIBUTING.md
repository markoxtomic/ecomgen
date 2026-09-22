# Contributing to ecomgen

Thanks for helping out. `ecomgen` generates synthetic e-commerce datasets that people
feed into BI pipelines, agents and Shopify imports, so the bar is less "does it run"
and more "is the data still trustworthy". This page covers the setup, the invariants
that must hold, and what a reviewable change looks like.

## Setup

Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## The checks

Run all three before opening a pull request. CI runs the same ones on
Ubuntu and Windows against Python 3.11 and 3.14.

```bash
python -m pytest                    # whole suite
python -m ruff check .              # lint
python -m ruff format --check .     # formatting (line length 100)
```

A quick end-to-end sanity check:

```bash
ecomgen generate --customers 200 --months 2 --out /tmp/demo
ecomgen validate --path /tmp/demo
```

## Invariants that must not break

These are the properties consumers rely on. If a change touches one, it needs a test
that proves the new behaviour and a note in the pull request.

1. **Determinism.** The same seed, code and dependency versions must produce
   byte-identical files, on every operating system. All randomness comes from the one
   seeded NumPy generator passed through the pipeline, plus the per-market Faker
   instances seeded from it. Never call `random`, `np.random` module functions, or
   anything that depends on wall-clock time, dict iteration order of unsorted input,
   or the host's locale.
2. **Gross, VAT-inclusive accounting.** Prices are what a customer pays:
   `total = subtotal - discount + shipping`, and `tax` is the VAT *contained in*
   `total`, never added on top. `cost_ratio_range` is a share of net revenue, so costs
   are drawn from the net catalog price. `ecomgen validate` enforces this.
3. **The manifest is authoritative.** Every export carries `manifest.json` with per
   file row counts and SHA-256 digests, and no timestamps, so it stays reproducible.
   `validate` fails if a digest, a row count or the metadata disagrees.
4. **Exports are atomic.** Generation writes to a staging directory and swaps it into
   place. A failure or a Ctrl+C must leave the destination untouched, never partially
   written.
5. **Errors versus warnings.** In `validate`, an integrity failure is an *error* and
   exits non-zero. A self-consistent but implausible metric is a *warning* and leaves
   the exit status at 0, so CI can gate on errors alone.
6. **Line endings.** CSV files are CRLF (the `csv` module writes them that way on all
   platforms) and JSON files are LF. `.gitattributes` marks `examples/sample_output/**`
   as `-text` so git stores those bytes verbatim; without it their digests break on
   checkout. Note that gitattributes applies the **last** matching pattern, so any new
   rule must stay below the generic `* text=auto`.

## Tests

- Tests live in `tests/`, named `test_*.py`. They are not shipped in the installed
  package.
- **Write the test first and watch it fail.** A regression test that never failed
  against the unfixed code proves nothing. Say so in the pull request.
- Name the report finding in the test module's docstring when you fix one, e.g.
  "report issue m4", so the history stays traceable.
- Tests that assert on CLI output must use the helpers in `tests/conftest.py`
  (`PLAIN_CONSOLE` and `plain()`). Rich wraps to the terminal width and colours its
  output, and CI's console is narrow and coloured, so raw substring checks are
  flaky there. Reproduce that environment locally with:

  ```bash
  COLUMNS=80 FORCE_COLOR=1 python -m pytest
  ```

- Keep the suite fast. Prefer a few hundred customers over a few thousand unless the
  behaviour under test needs the volume.

## Changing generated output

Plenty of legitimate changes alter the bytes a fixed seed produces. When that happens:

1. Say so explicitly in the pull request, and describe which columns or tables move.
   People pin seeds as test fixtures; a silent change breaks them.
2. Regenerate the committed sample so it matches the code:

   ```bash
   ecomgen generate --preset garden-decor --markets de,at,fr --customers 500 \
     --months 12 --start-date 2024-01-01 --seed 42 --format all --shopify-export \
     --out examples/sample_output
   ecomgen validate --path examples/sample_output
   ```

3. Bump the version in `pyproject.toml` and `ecomgen/__init__.py` when the schema or
   the semantics change, not just the numbers.

## Adding a preset or a market

Presets are bundled package resources in `ecomgen/config/presets/`; the CLI does not
load arbitrary paths. The README's [Add a preset](README.md#add-a-preset) section has
the full field list. In short: copy an existing preset, set the top-level `name` to the
filename stem, fill in every field, then run `ecomgen presets` and the config tests.

Markets live in `ecomgen/config/markets.yaml`. A new market needs its currency, VAT
rate, FX rate, shipping cost, demand weight, Faker locale, IANA time zone, 24 hourly
order weights, and the behaviour multipliers (`cac_multiplier`,
`return_rate_multiplier`, `repeat_multiplier`) that keep markets from behaving
identically.

Configuration is validated strictly: unknown fields are rejected, ranges must be
ordered and in bounds, and the channel weights must sum to 1. Malformed YAML should
produce a clear `ValueError`, never a traceback.

## Pull requests

- One logical change per commit, with a message that says what broke and why the fix
  is right. The existing history uses `fix(<id>): <summary>` where `<id>` is a report
  finding; `feat:`, `docs:`, `chore:` and `test:` are fine otherwise.
- Include the failing-first evidence, the check results, and any output changes.
- Update the README when you change behaviour it documents.
- `reports/` holds point-in-time audit reports. Treat them as a historical record:
  add a new one rather than rewriting an old one.

## Reporting bugs

Use the issue templates. The two things that make a data bug reproducible are the
**exact command including `--seed`** and the **`manifest.json`** from the affected
export. Wrong-but-plausible data matters more than a crash: a crash is visible, while
a silently wrong number ends up in someone's dashboard.

## License

By contributing you agree that your contributions are licensed under the repository's
[MIT license](LICENSE).
