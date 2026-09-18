"""Command-line interface for generation, export, and validation."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ecomgen.config import available_presets
from ecomgen.exporters import check_output_dir, export_dataset
from ecomgen.pipeline import (
    DEFAULT_CUSTOMERS,
    DEFAULT_MARKETS,
    DEFAULT_MONTHS,
    DEFAULT_PRESET,
    DEFAULT_SEED,
    DEFAULT_START_DATE,
    generate_dataset,
)
from ecomgen.schemas import Dataset
from ecomgen.validation import validate_path

app = typer.Typer(
    help="Generate realistic synthetic e-commerce datasets.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


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
    customers: Annotated[int, typer.Option("--customers", min=0)] = DEFAULT_CUSTOMERS,
    months: Annotated[int, typer.Option("--months", min=1)] = DEFAULT_MONTHS,
    start_date: Annotated[str, typer.Option("--start-date")] = DEFAULT_START_DATE.isoformat(),
    seed: Annotated[int, typer.Option("--seed")] = DEFAULT_SEED,
    out: Annotated[Path, typer.Option("--out")] = Path("dataset"),
    format: Annotated[ExportFormat, typer.Option("--format")] = ExportFormat.csv,
    shopify_export: Annotated[bool, typer.Option("--shopify-export")] = False,
) -> None:
    """Generate and export a complete synthetic dataset."""

    market_codes = tuple(code.strip().lower() for code in markets.split(",") if code.strip())
    try:
        dataset = _generate_and_export(
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
        console.print(f"[red]Error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from None

    console.print(f"Exported to {out}")
    _summary(dataset)


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
) -> Dataset:
    parsed_start_date = date.fromisoformat(start_date)
    check_output_dir(out)
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Generating dataset", total=None)
        dataset = generate_dataset(
            preset=preset,
            markets=market_codes,
            customers=customers,
            months=months,
            start_date=parsed_start_date,
            seed=seed,
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
    )
    return dataset


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

    report = validate_path(path)
    if not report.valid:
        console.print("[red]Validation failed:[/red]")
        for error in report.errors:
            console.print(f"  - {error}", highlight=False)
        raise typer.Exit(code=1)

    total = sum(report.row_counts.values())
    console.print(f"[green]Valid dataset[/green]: {total:,} rows across 7 tables")


if __name__ == "__main__":
    app()
