from decimal import Decimal

import pytest

from shop import Cart


def test_subtotal_sums_line_items():
    cart = Cart()
    cart.add("TEA", "120.50", 2)
    cart.add("MUG", 399)
    assert cart.subtotal() == Decimal("640.00")


def test_quantity_must_be_positive():
    with pytest.raises(ValueError):
        Cart().add("TEA", 10, 0)
