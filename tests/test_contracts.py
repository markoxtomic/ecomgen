from ecomgen.errors import GenerationWarning, StockoutError
from ecomgen.pipeline import generate_dataset


def test_progress_callback_reaches_total() -> None:
    calls: list[tuple[int, int]] = []

    generate_dataset(
        customers=20, months=1, progress=lambda done, total: calls.append((done, total))
    )

    assert calls
    assert calls[-1][0] == calls[-1][1]
    assert all(0 <= done <= total for done, total in calls)


def test_error_types_fit_existing_cli_handling() -> None:
    assert issubclass(StockoutError, ValueError)
    assert issubclass(GenerationWarning, UserWarning)
