"""Money helpers shared by the shop (INR, 2 decimals, half-up)."""
from decimal import ROUND_HALF_UP, Decimal


def to_money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
