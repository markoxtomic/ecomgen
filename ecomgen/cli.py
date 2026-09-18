"""Command-line interface for generation, export, and validation."""

import sys
import warnings
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from ecomgen.config import available_presets
from ecomgen.errors import GenerationWarning
from ecomgen.exporters import check_output_dir, export_dataset
from ecomgen.pipeline import (
    DEFAULT_CUSTOMERS,
    DEFAULT_MARKETS,
    DEFAULT_MONTHS,
    DEFAULT_PRESET,
    DEFAULT_SEED,
    DEFAULT_START_DATE,
    generate_dataset,
    manifest_metadata,
)
from ecomgen.schemas import Dataset
from ecomgen.validation import validate_path


def _use_utf8_output() -> None:
    """Switch the process's stdout and stderr to UTF-8 where possible.

    Redirected Windows streams (a file, a pipe, or ``NUL``) default to the ANSI
    code page, which cannot encode Rich's spinner and box glyphs. ``errors=
    "replace"`` guarantees that console output can never crash the program.
    Streams replaced by a caller (e.g. a test runner) are left alone.
    """

    for stream, original in ((sys.stdout, sys.__stdout__), (sys.stderr, sys.__stderr__)):
        if stream is None or stream is not original:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_use_utf8_output()

MAX_CUSTOMERS = 1_000_000
MAX_MONTHS = 120
# Returns are dated up to 30 days after their order, so dates up to this many
# days past the generation window must still be representable.
RETURN_WINDOW_DAYS = 30

app = typer.Typer(
    help="Generate realistic synthetic e-commerce datasets.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
# The legacy Windows renderer drives the console through the Win32 API and
# mis-detects NUL as a legacy console; plain ANSI output works on every
# supported Windows version.
console = Console(legacy_windows=False)


def _ascii_only() -> bool:
    """Whether the console's encoding cannot represent Unicode symbols.

    Rich already falls back to ASCII boxes and bars in that case; spinners must
    be chosen accordingly.
    """

    return not console.encoding.lower().startswith("utf")


class ExportFormat(str, Enum):
    csv = "csv"
    json = "json"
    all = "all"


@app.callback()
def main() -> None:
    """Synthetic e-commerce dataset generator."""


def _summary(dataset: Dataset) -> None:
    table = Table(title="Generated dataset")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for name, records in dataset.tables().items():
        table.add_row(name, f"{len(records):,}")
    console.print(table)

    revenue: defaultdict[str, Decimal] = defaultdict(Decimal)
    for order in dataset.orders:
        revenue[order.market] += order.total
    if revenue:
        console.print(
            "Revenue: "
            + ", ".join(
                f"{market.upper()} {amount:,.2f}" for market, amount in sorted(revenue.items())
            )
        )
    return_rate = len(dataset.returns) / len(dataset.order_items) if dataset.order_items else 0.0
    repeat_rate = (
        sum(order.is_repeat for order in dataset.orders) / len(dataset.orders)
        if dataset.orders
        else 0.0
    )
    console.print(f"Return rate: {return_rate:.1%}  Repeat rate: {repeat_rate:.1%}")


@app.command()
def generate(
    preset: Annotated[str, typer.Option("--preset")] = DEFAULT_PRESET,
    markets: Annotated[str, typer.Option("--markets")] = ",".join(DEFAULT_MARKETS),
    customers: Annotated[
        int,
        typer.Option(
            "--customers",
            min=0,
            max=MAX_CUSTOMERS,
            help=f"Number of customers, 0 to {MAX_CUSTOMERS:,}.",
        ),
    ] = DEFAULT_CUSTOMERS,
    months: Annotated[
        int,
        typer.Option(
            "--months",
            min=1,
            max=MAX_MONTHS,
            help=f"Length of the generation window in months, 1 to {MAX_MONTHS}.",
        ),
    ] = DEFAULT_MONTHS,
    start_date: Annotated[str, typer.Option("--start-date")] = DEFAULT_START_DATE.isoformat(),
    seed: Annotated[
        int, typer.Option("--seed", min=0, help="Random seed; a non-negative integer.")
    ] = DEFAULT_SEED,
    out: Annotated[Path, typer.Option("--out")] = Path("dataset"),
    format: Annotated[ExportFormat, typer.Option("--format")] = ExportFormat.csv,
    shopify_export: Annotated[bool, typer.Option("--shopify-export")] = False,
) -> None:
    """Generate and export a complete synthetic dataset."""

    market_codes = tuple(code.strip().lower() for code in markets.split(",") if code.strip())
    try:
        dataset, generation_warnings = _generate_and_export(
            preset=preset,
            market_codes=market_codes,
            customers=customers,
            months=months,
            start_date=start_date,
            seed=seed,
            out=out,
            format=format,
            shopify_export=shopify_export,
        )
    except KeyboardInterrupt:
        console.print("[red]Aborted[/red]: no output was written to the destination.")
        raise typer.Exit(code=130) from None
    except (OSError, ValueError) as exc:
        # StockoutError is a ValueError: it ends here with a clean one-line error.
        console.print(f"[red]Error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from None

    console.print(f"Exported to {out}")
    _summary(dataset)
    for message in generation_warnings:
        console.print(f"[bold yellow]Warning:[/bold yellow] {escape(message)}", highlight=False)


def _check_window(start: date, months: int) -> None:
    """Reject windows whose dates (including later returns) pass 9999-12-31."""

    month_index = start.month - 1 + months
    end_year = start.year + month_index // 12
    end = None
    if end_year <= date.max.year:
        end_month = month_index % 12 + 1
        end = date(end_year, end_month, min(start.day, monthrange(end_year, end_month)[1]))
    if end is None or (date.max - end).days < RETURN_WINDOW_DAYS:
        raise ValueError(
            f"--start-date {start.isoformat()} with --months {months} runs past "
            f"{date.max.isoformat()}, the latest supported date (the window plus "
            f"{RETURN_WINDOW_DAYS} days for returns); use an earlier --start-date or "
            "fewer --months"
        )


def _generate_and_export(
    *,
    preset: str,
    market_codes: tuple[str, ...],
    customers: int,
    months: int,
    start_date: str,
    seed: int,
    out: Path,
    format: ExportFormat,
    shopify_export: bool,
) -> tuple[Dataset, list[str]]:
    """Generate and export; return the dataset and any generation warnings."""

    parsed_start_date = date.fromisoformat(start_date)
    _check_window(parsed_start_date, months)
    check_output_dir(out)
    # Rich switches the bar to ASCII itself on narrow encodings. Off a terminal
    # (pipes, files, CI logs) the bar is disabled so nothing is written at all.
    with Progress(
        SpinnerColumn("line" if _ascii_only() else "dots"),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        disable=not console.is_terminal,
    ) as progress:
        task = progress.add_task("Generating dataset", total=None)

        def report(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)
            progress.refresh()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", GenerationWarning)
            dataset = generate_dataset(
                preset=preset,
                markets=market_codes,
                customers=customers,
                months=months,
                start_date=parsed_start_date,
                seed=seed,
                progress=report,
            )
    generation_warnings: list[str] = []
    for caught_warning in caught:
        if issubclass(caught_warning.category, GenerationWarning):
            generation_warnings.append(str(caught_warning.message))
        else:
            warnings.warn_explicit(
                caught_warning.message,
                caught_warning.category,
                caught_warning.filename,
                caught_warning.lineno,
                source=caught_warning.source,
            )
    formats = {
        ExportFormat.csv: ("csv",),
        ExportFormat.json: ("json",),
        ExportFormat.all: ("csv", "json"),
    }[format]
    export_dataset(
        dataset,
        out,
        formats=formats,
        shopify=shopify_export,
        arguments={
            "preset": preset,
            "markets": list(dict.fromkeys(market_codes)),
            "customers": customers,
            "months": months,
            "start_date": parsed_start_date.isoformat(),
            "seed": seed,
            "format": format.value,
            "shopify_export": shopify_export,
        },
        metadata=manifest_metadata(parsed_start_date, months),
    )
    return dataset, generation_warnings


@app.command("presets")
def list_presets() -> None:
    """List bundled generation presets."""

    for name in available_presets():
        console.print(name)


@app.command()
def validate(
    path: Annotated[Path, typer.Option("--path", exists=True, readable=True)],
) -> None:
    """Validate an existing CSV or JSON dataset export."""

    report = validate_path(path, require_manifest=True)
    if not report.valid:
        console.print("[red]Validation failed:[/red]")
        for error in report.errors:
            console.print(f"  - {error}", highlight=False)
        raise typer.Exit(code=1)

    total = sum(report.row_counts.values())
    console.print(f"[green]Valid dataset[/green]: {total:,} rows across 7 tables")


if __name__ == "__main__":
    app()
