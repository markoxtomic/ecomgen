"""Regression tests for cross-table semantic validation."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from ecomgen.exporters import export_json
from ecomgen.exporters.manifest import write_manifest
from ecomgen.schemas import (
    Customer,
    Dataset,
    MarketingSpend,
    Order,
    OrderItem,
    Product,
    Return,
    Variant,
)
from ecomgen.validation import validate_dataset, validate_path

CENT = Decimal("0.01")
VAT_RATE = Decimal("0.19")


def _tax_from_gross(gross: Decimal) -> Decimal:
    return (gross * VAT_RATE / (Decimal(1) + VAT_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)


def _order(
    *,
    order_id: str,
    created_at: datetime,
    discount: Decimal,
    discount_code: str | None,
    is_repeat: bool,
) -> Order:
    subtotal = Decimal("100.00")
    shipping = Decimal("4.90")
    total = subtotal - discount + shipping
    return Order(
        id=order_id,
        customer_id="cust-1",
        market="de",
        created_at=created_at,
        currency="EUR",
        subtotal=subtotal,
        discount=discount,
        shipping=shipping,
        tax=_tax_from_gross(total),
        total=total,
        discount_code=discount_code,
        is_repeat=is_repeat,
    )


@pytest.fixture
def valid_dataset() -> Dataset:
    customer_created = datetime(2024, 1, 1, 8, tzinfo=UTC)
    first_order = _order(
        order_id="ord-1",
        created_at=datetime(2024, 1, 2, 10, tzinfo=UTC),
        discount=Decimal("0.00"),
        discount_code=None,
        is_repeat=False,
    )
    repeat_order = _order(
        order_id="ord-2",
        created_at=datetime(2024, 1, 3, 10, tzinfo=UTC),
        discount=Decimal("10.00"),
        discount_code="SAVE10",
        is_repeat=True,
    )
    return Dataset(
        products=[
            Product(
                id="prod-1",
                title="Oak Planter",
                category="planters",
                description_short="A fixture product.",
                price_eur=Decimal("100.00"),
                cost_eur=Decimal("40.00"),
                markets=["de"],
            )
        ],
        variants=[
            Variant(
                id="var-1",
                product_id="prod-1",
                sku="FIXTURE-1",
                option_name="Size",
                option_value="One Size",
                price_eur=Decimal("100.00"),
                inventory=10,
            )
        ],
        customers=[
            Customer(
                id="cust-1",
                market="de",
                email="fixture@example.test",
                first_name="Test",
                last_name="Customer",
                city="Berlin",
                created_at=customer_created,
                acquisition_channel="meta",
            )
        ],
        # Deliberately not grouped by customer chronology. Validation must derive
        # repeat status from (created_at, id), not input row order.
        orders=[repeat_order, first_order],
        order_items=[
            OrderItem(
                id="item-2",
                order_id="ord-2",
                variant_id="var-1",
                quantity=1,
                unit_price=Decimal("100.00"),
            ),
            OrderItem(
                id="item-1",
                order_id="ord-1",
                variant_id="var-1",
                quantity=1,
                unit_price=Decimal("100.00"),
            ),
        ],
        returns=[
            Return(
                id="ret-1",
                order_id="ord-2",
                order_item_id="item-2",
                reason="changed_mind",
                refund_amount=Decimal("90.00"),
                created_at=repeat_order.created_at + timedelta(days=2),
            )
        ],
        marketing_spend=[
            MarketingSpend(
                date=customer_created.date(),
                market="de",
                channel="meta",
                currency="EUR",
                spend=Decimal("25.00"),
                impressions=1_000,
                clicks=50,
                new_customers=1,
            )
        ],
    )


def _replace_order(dataset: Dataset, order_id: str, **updates) -> Dataset:
    orders = [
        order.model_copy(update=updates) if order.id == order_id else order
        for order in dataset.orders
    ]
    return replace(dataset, orders=orders)


def _assert_invalid(dataset: Dataset, *message_parts: str) -> None:
    report = validate_dataset(dataset)
    assert not report.valid
    message = "\n".join(report.errors).lower()
    for part in message_parts:
        assert part.lower() in message, report.errors


def test_valid_semantic_fixture_is_accepted(valid_dataset: Dataset) -> None:
    assert validate_dataset(valid_dataset).errors == []


@pytest.mark.parametrize(
    ("updates", "message_parts"),
    [
        ({"market": "xx"}, ("market", "xx")),
        ({"currency": "GBP"}, ("currency",)),
        ({"tax": Decimal("0.01")}, ("vat",)),
        (
            {
                "shipping": Decimal("5.00"),
                "tax": _tax_from_gross(Decimal("105.00")),
                "total": Decimal("105.00"),
            },
            ("shipping 5.00", "4.90"),
        ),
    ],
    ids=["unknown-market", "wrong-currency", "wrong-vat", "wrong-shipping"],
)
def test_order_market_currency_vat_and_shipping_are_validated(
    valid_dataset: Dataset, updates: dict, message_parts: tuple[str, ...]
) -> None:
    _assert_invalid(_replace_order(valid_dataset, "ord-1", **updates), *message_parts)


def test_customer_and_order_markets_must_match(valid_dataset: Dataset) -> None:
    customer = valid_dataset.customers[0].model_copy(update={"market": "fr"})
    _assert_invalid(replace(valid_dataset, customers=[customer]), "customer", "market")


def test_order_cannot_precede_customer(valid_dataset: Dataset) -> None:
    created_at = valid_dataset.customers[0].created_at - timedelta(seconds=1)
    _assert_invalid(
        _replace_order(valid_dataset, "ord-1", created_at=created_at),
        "order",
        "customer",
    )


def test_repeat_status_is_derived_from_deterministic_chronology(
    valid_dataset: Dataset,
) -> None:
    first = next(order for order in valid_dataset.orders if order.id == "ord-1")
    second = next(order for order in valid_dataset.orders if order.id == "ord-2")
    shared_time = datetime(2024, 1, 2, 10, tzinfo=UTC)
    tied = replace(
        valid_dataset,
        orders=[
            second.model_copy(update={"created_at": shared_time, "is_repeat": True}),
            first.model_copy(update={"created_at": shared_time, "is_repeat": False}),
        ],
    )
    assert validate_dataset(tied).valid

    swapped_flags = replace(
        tied,
        orders=[
            tied.orders[0].model_copy(update={"is_repeat": False}),
            tied.orders[1].model_copy(update={"is_repeat": True}),
        ],
    )
    _assert_invalid(swapped_flags, "is_repeat")


@pytest.mark.parametrize(
    ("order_id", "discount_code"),
    [
        ("ord-1", "SAVE10"),
        ("ord-2", None),
        ("ord-2", "save-ten"),
    ],
    ids=["code-without-discount", "discount-without-code", "malformed-code"],
)
def test_discount_code_exists_iff_discount_and_matches_save_number(
    valid_dataset: Dataset, order_id: str, discount_code: str | None
) -> None:
    _assert_invalid(
        _replace_order(valid_dataset, order_id, discount_code=discount_code),
        "discount",
        "code",
    )


def test_sold_product_must_be_eligible_in_order_market(valid_dataset: Dataset) -> None:
    product = valid_dataset.products[0].model_copy(update={"markets": ["fr"]})
    _assert_invalid(replace(valid_dataset, products=[product]), "product", "market")


def test_return_must_be_strictly_after_order(valid_dataset: Dataset) -> None:
    returned = valid_dataset.returns[0]
    order = next(order for order in valid_dataset.orders if order.id == returned.order_id)
    _assert_invalid(
        replace(
            valid_dataset,
            returns=[returned.model_copy(update={"created_at": order.created_at})],
        ),
        "return",
        "order",
    )


def test_cumulative_refund_cap_uses_order_level_cent_allocation(
    valid_dataset: Dataset,
) -> None:
    order = next(order for order in valid_dataset.orders if order.id == "ord-2")
    order = order.model_copy(
        update={
            "subtotal": Decimal("0.03"),
            "discount": Decimal("0.01"),
            "total": Decimal("4.92"),
            "tax": _tax_from_gross(Decimal("4.92")),
            "discount_code": "SAVE33",
        }
    )
    items = [
        OrderItem(
            id=item_id,
            order_id=order.id,
            variant_id="var-1",
            quantity=1,
            unit_price=Decimal("0.01"),
        )
        for item_id in ("item-c", "item-a", "item-b")
    ]
    baseline = replace(
        valid_dataset,
        orders=[
            order if candidate.id == order.id else candidate for candidate in valid_dataset.orders
        ],
        order_items=[valid_dataset.order_items[1], *items],
        returns=[],
    )
    assert validate_dataset(baseline).valid

    split_returns = [
        Return(
            id=f"ret-split-{number}",
            order_id=order.id,
            order_item_id="item-a",
            reason="changed_mind",
            refund_amount=Decimal("0.01"),
            created_at=order.created_at + timedelta(days=number + 1),
        )
        for number in range(2)
    ]

    report = validate_dataset(replace(baseline, returns=split_returns))

    assert report.errors == ["order item item-a: cumulative refunds 0.02 exceed item value 0.00"]


def test_return_cannot_exceed_manifest_cutoff(valid_dataset: Dataset, tmp_path) -> None:
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)
    returned = valid_dataset.returns[0].model_copy(
        update={"created_at": cutoff + timedelta(seconds=1)}
    )
    export_json(replace(valid_dataset, returns=[returned]), tmp_path)
    manifest_path = write_manifest(
        tmp_path,
        {
            "preset": "garden-decor",
            "markets": ["de"],
            "customers": 1,
            "months": 1,
            "start_date": "2024-01-01",
            "seed": 1,
            "format": "json",
            "shopify_export": False,
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metadata"] = {"return_cutoff": cutoff.date().isoformat()}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_path(tmp_path)

    assert not report.valid
    assert "cutoff" in "\n".join(report.errors).lower(), report.errors


def test_marketing_rows_have_unique_date_market_channel_key(valid_dataset: Dataset) -> None:
    duplicate = valid_dataset.marketing_spend[0].model_copy()
    _assert_invalid(
        replace(valid_dataset, marketing_spend=[*valid_dataset.marketing_spend, duplicate]),
        "marketing",
        "duplicate",
    )


@pytest.mark.parametrize(
    ("updates", "message_parts"),
    [
        ({"impressions": 49}, ("impressions", "clicks")),
        ({"clicks": 0, "new_customers": 1}, ("clicks", "new_customers")),
    ],
    ids=["clicks-exceed-impressions", "customers-exceed-clicks"],
)
def test_marketing_funnel_counts_are_monotonic(
    valid_dataset: Dataset, updates: dict, message_parts: tuple[str, ...]
) -> None:
    row = valid_dataset.marketing_spend[0].model_copy(update=updates)
    _assert_invalid(
        replace(valid_dataset, marketing_spend=[row]),
        *message_parts,
    )


def test_marketing_currency_matches_market(valid_dataset: Dataset) -> None:
    row = valid_dataset.marketing_spend[0].model_copy(update={"currency": "GBP"})
    _assert_invalid(replace(valid_dataset, marketing_spend=[row]), "marketing", "currency")


def test_marketing_new_customers_reconcile_to_customer_acquisition(
    valid_dataset: Dataset,
) -> None:
    row = valid_dataset.marketing_spend[0].model_copy(update={"new_customers": 0})
    _assert_invalid(
        replace(valid_dataset, marketing_spend=[row]),
        "new_customers",
        "customer",
    )


def test_mixed_naive_and_aware_datetimes_are_compared_safely(
    valid_dataset: Dataset,
) -> None:
    customer = valid_dataset.customers[0]
    returned = valid_dataset.returns[0]
    mixed = replace(
        valid_dataset,
        customers=[
            customer.model_copy(update={"created_at": customer.created_at.replace(tzinfo=None)})
        ],
        returns=[
            returned.model_copy(update={"created_at": returned.created_at.replace(tzinfo=None)})
        ],
    )

    assert validate_dataset(mixed).valid


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "cannot read products.json"),
        ("{}", "products.json must contain a JSON array of objects"),
        ("[{}]", "products row 1"),
    ],
    ids=["invalid-json", "non-array-json", "invalid-record"],
)
def test_validate_path_reports_actionable_malformed_json_table_errors(
    valid_dataset: Dataset, tmp_path, payload: str, message: str
) -> None:
    export_json(valid_dataset, tmp_path)
    (tmp_path / "products.json").write_text(payload, encoding="utf-8")

    report = validate_path(tmp_path)

    assert not report.valid
    assert message in "\n".join(report.errors), report.errors


def test_validate_path_reports_incomplete_representation(tmp_path) -> None:
    (tmp_path / "products.json").write_text("[]", encoding="utf-8")

    report = validate_path(tmp_path)

    assert not report.valid
    combined = "\n".join(report.errors)
    assert "incomplete JSON representation" in combined
    assert "no complete CSV or JSON dataset representation" in combined


def test_validate_path_requires_metadata_for_current_manifest(
    valid_dataset: Dataset, tmp_path
) -> None:
    export_json(valid_dataset, tmp_path)
    write_manifest(
        tmp_path,
        {
            "preset": "garden-decor",
            "markets": ["de"],
            "customers": 1,
            "months": 1,
            "start_date": "2024-01-01",
            "seed": 1,
            "format": "json",
            "shopify_export": False,
        },
    )

    report = validate_path(tmp_path)

    assert not report.valid
    assert "current exports require semantic metadata" in "\n".join(report.errors)


def test_validate_path_reports_invalid_manifest_metadata(valid_dataset: Dataset, tmp_path) -> None:
    export_json(valid_dataset, tmp_path)
    write_manifest(
        tmp_path,
        {"seed": 1},
        metadata={
            "schema_version": 2,
            "pricing_mode": "net",
            "order_window_start": "2024-03-01",
            "order_window_end": "2024-02-01",
            "return_cutoff": "2024-01-01",
            "return_delay_days": {"min": 30, "max": 3},
        },
    )

    report = validate_path(tmp_path)

    combined = "\n".join(report.errors)
    assert "unsupported metadata schema_version" in combined
    assert "pricing_mode must be 'gross_vat_inclusive'" in combined
    assert "return_delay_days must be an ordered range" in combined
    assert "window dates are not ordered" in combined


def test_validate_dataset_reports_orphaned_cross_table_references(
    valid_dataset: Dataset,
) -> None:
    variant = valid_dataset.variants[0].model_copy(update={"product_id": "missing-product"})
    item = valid_dataset.order_items[0].model_copy(
        update={"order_id": "missing-order", "variant_id": "missing-variant"}
    )
    mismatched_return = valid_dataset.returns[0].model_copy(update={"order_id": "ord-1"})
    orphaned_return = valid_dataset.returns[0].model_copy(
        update={
            "id": "ret-2",
            "order_id": "missing-order",
            "order_item_id": "missing-item",
        }
    )
    malformed = replace(
        valid_dataset,
        variants=[variant],
        order_items=[item, valid_dataset.order_items[1]],
        returns=[mismatched_return, orphaned_return],
    )

    report = validate_dataset(malformed)

    combined = "\n".join(report.errors)
    assert "orphan product_id missing-product" in combined
    assert "orphan order_id missing-order" in combined
    assert "orphan variant_id missing-variant" in combined
    assert "order does not match its order item" in combined
    assert "orphan order_item_id missing-item" in combined
