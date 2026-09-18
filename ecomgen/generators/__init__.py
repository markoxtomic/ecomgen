"""Pure dataset generators."""

from .customers import generate_customers
from .marketing import generate_marketing
from .orders import OrderGenerationResult, generate_orders
from .products import generate_products
from .returns import generate_returns

__all__ = [
    "OrderGenerationResult",
    "generate_customers",
    "generate_marketing",
    "generate_orders",
    "generate_products",
    "generate_returns",
]
