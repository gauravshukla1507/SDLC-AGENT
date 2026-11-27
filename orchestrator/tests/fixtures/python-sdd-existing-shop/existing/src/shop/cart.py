"""Shopping cart: line items and subtotal."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .money import to_money


@dataclass(frozen=True)
class LineItem:
    sku: str
    unit_price: Decimal
    quantity: int = 1

    @property
    def amount(self) -> Decimal:
        return to_money(self.unit_price * self.quantity)


@dataclass
class Cart:
    items: list[LineItem] = field(default_factory=list)

    def add(self, sku: str, unit_price, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        self.items.append(LineItem(sku, to_money(unit_price), quantity))

    def subtotal(self) -> Decimal:
        return to_money(sum((i.amount for i in self.items), Decimal("0")))
