"""Pydantic record schemas shared by generators and exporters."""

from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """Base for immutable-shape dataset records."""

    model_config = ConfigDict(extra="forbid")


class Product(Record):
    id: str
    title: str
    category: str
    description_short: str
    price_eur: Decimal = Field(ge=0)
    cost_eur: Decimal = Field(ge=0)
    markets: list[str] = Field(min_length=1)


class Variant(Record):
    id: str
    product_id: str
    sku: str
    option_name: str
    option_value: str
    price_eur: Decimal = Field(ge=0)
    inventory: int = Field(ge=0)


class Customer(Record):
    id: str
    market: str
    email: str
    first_name: str
    last_name: str
    city: str
    created_at: datetime
    acquisition_channel: str


class Order(Record):
    id: str
    customer_id: str
    market: str
    created_at: datetime
    currency: str
    fx_rate_from_eur: Decimal = Field(gt=0)
    subtotal: Decimal = Field(ge=0)
    discount: Decimal = Field(ge=0)
    shipping: Decimal = Field(ge=0)
    tax: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)
    discount_code: str | None
    is_repeat: bool


class OrderItem(Record):
    id: str
    order_id: str
    variant_id: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class Return(Record):
    id: str
    order_id: str
    order_item_id: str
    reason: str
    refund_amount: Decimal = Field(ge=0)
    created_at: datetime


class MarketingSpend(Record):
    """Daily channel performance; ``spend`` is in the market ``currency``.

    ``new_customers`` counts the customers this channel acquired in this market on
    this date, i.e. customers with that ``created_at`` date and ``acquisition_channel``.
    """

    date: date
    market: str
    channel: str
    currency: str
    fx_rate_from_eur: Decimal = Field(gt=0)
    spend: Decimal = Field(ge=0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    new_customers: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class Dataset:
    """Complete generated dataset, grouped by its export tables."""

    products: list[Product]
    variants: list[Variant]
    customers: list[Customer]
    orders: list[Order]
    order_items: list[OrderItem]
    returns: list[Return]
    marketing_spend: list[MarketingSpend]

    TABLES: ClassVar[tuple[str, ...]] = (
        "products",
        "variants",
        "customers",
        "orders",
        "order_items",
        "returns",
        "marketing_spend",
    )

    def tables(self) -> dict[str, list[Record]]:
        """Return tables in their stable export order."""

        return {field.name: getattr(self, field.name) for field in fields(self)}
