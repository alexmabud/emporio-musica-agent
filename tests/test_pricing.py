"""Behavior tests for the price, PIX and installment rules of the policy manual."""

from decimal import Decimal

import pytest

from emporio_agent.pricing import (
    combined_payment_allowed,
    format_brl,
    installment_options,
    min_installment_value,
    payment_options,
    pix_price,
    price_product,
)


def test_promotion_is_applied_to_the_list_price() -> None:
    """A 18% promotion on R$ 2.399 gives R$ 1.967,18; the list price is kept for transparency."""
    quote = price_product(2399, 18)
    assert quote.list_price == Decimal("2399.00")
    assert quote.promo_percent == Decimal("18")
    assert quote.final_price == Decimal("1967.18")


def test_without_promotion_the_final_price_is_the_list_price() -> None:
    """No promotion means no discount and no promo percent to show."""
    quote = price_product(2199, None)
    assert quote.final_price == Decimal("2199.00")
    assert quote.promo_percent is None


@pytest.mark.parametrize(
    ("list_price", "promo_percent"),
    [(-1, None), (100, -5), (100, 101)],
)
def test_invalid_prices_and_percents_are_rejected(
    list_price: int, promo_percent: int | None
) -> None:
    """Negative prices and promotions outside 0-100% are programming/data errors, not quotes."""
    with pytest.raises(ValueError, match="must be"):
        price_product(list_price, promo_percent)


def test_pix_gives_five_percent_off_the_list_price() -> None:
    """Takamine GD20 (no promotion) costs R$ 2.199 and R$ 2.089,05 on PIX."""
    assert pix_price(2199, None) == Decimal("2089.05")


def test_pix_discount_does_not_stack_on_a_promotion() -> None:
    """Policy 6.2: PIX does not apply to promotional prices (Ohana CK-20, 20% off)."""
    assert pix_price(549, 20) is None


def test_pix_price_rounds_half_up_to_the_cent() -> None:
    """R$ 10,30 less 5% is exactly R$ 9,785 and rounds up to R$ 9,79 (not to the even R$ 9,78)."""
    assert pix_price("10.30", None) == Decimal("9.79")


@pytest.mark.parametrize(
    ("amount", "max_count", "last_value"),
    [
        (30, 1, "30.00"),  # below R$ 50: only one payment
        (90, 1, "90.00"),  # 2x would be R$ 45, under the R$ 50 floor
        (150, 3, "50.00"),  # up to 3x: R$ 50 per installment
        (479, 5, "95.80"),  # 6x would be R$ 79,83, under the R$ 80 floor for 4-6x
        (1000, 10, "100.00"),  # 7-12x: R$ 100 per installment; 11x would be R$ 90,91
        (5999, 12, "499.91"),  # Taylor 110e: full 12x (first one carries the cents left over)
    ],
)
def test_installments_respect_the_minimum_per_installment(
    amount: int, max_count: int, last_value: str
) -> None:
    """Up to 3x needs R$ 50 per installment, 4-6x R$ 80 and 7-12x R$ 100, never beyond 12x."""
    options = installment_options(amount)
    assert [option.count for option in options] == list(range(1, max_count + 1))
    assert options[-1].value == Decimal(last_value)
    assert all(option.value >= min_installment_value(option.count) for option in options)


@pytest.mark.parametrize(
    ("amount", "allowed"),
    [("1999.99", False), ("2000", False), ("2000.01", True), (8732, True)],
)
def test_combined_payment_is_only_allowed_above_two_thousand(
    amount: str | int, allowed: bool
) -> None:
    """Policy 3.1: mixing payment methods (e.g. PIX + card) needs a purchase above R$ 2.000,00."""
    assert combined_payment_allowed(amount) is allowed


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (2199, "R$ 2.199,00"),
        ("1967.18", "R$ 1.967,18"),
        (Decimal("0.5"), "R$ 0,50"),
        (1234567.5, "R$ 1.234.567,50"),
    ],
)
def test_amounts_are_formatted_the_brazilian_way(value: object, text: str) -> None:
    """Customers see 'R$ 2.199,00': dot for thousands, comma for cents, two decimals always."""
    assert format_brl(value) == text


def test_payment_options_for_a_product_without_promotion() -> None:
    """Takamine GD20: PIX discount, full 12x and combined payment (above R$ 2.000)."""
    options = payment_options(2199, None)
    assert options.price.final_price == Decimal("2199.00")
    assert options.pix_price == Decimal("2089.05")
    assert options.installments[-1].count == 12
    assert options.combined_payment_allowed


def test_payment_options_for_a_product_on_promotion() -> None:
    """Ohana CK-20 (20% off): no PIX discount, installments over the promotional price."""
    options = payment_options(549, 20)
    assert options.price.final_price == Decimal("439.20")
    assert options.pix_price is None
    assert [o.count for o in options.installments][-1] == 5  # 6x would be R$ 73,20 (< R$ 80)
    assert not options.combined_payment_allowed


def test_the_cents_left_over_go_to_the_first_installment() -> None:
    """R$ 1.000 in 3x is 1x of R$ 333,34 + 2x of R$ 333,33, as the real store does."""
    option = installment_options(1000)[2]
    assert option.count == 3
    assert option.first_value == Decimal("333.34")
    assert option.value == Decimal("333.33")


def test_installments_always_add_up_to_the_total() -> None:
    """For any amount and any plan, first + the others == total (no lost or invented cents)."""
    checked = 0
    for cents in range(5000, 600000, 997):  # includes repeating decimals such as 1000 / 3
        amount = Decimal(cents) / 100
        for option in installment_options(amount):
            assert option.first_value + option.value * (option.count - 1) == amount
            assert option.first_value >= option.value
            assert option.first_value - option.value < Decimal("0.01") * option.count
            checked += 1
    assert checked > 3000


@pytest.mark.parametrize(
    ("amount", "count", "text"),
    [
        (1000, 3, "3x: 1x de R$ 333,34 + 2x de R$ 333,33 (total R$ 1.000,00)"),
        (1000, 10, "10x de R$ 100,00 (total R$ 1.000,00)"),
        (30, 1, "1x de R$ 30,00 (total R$ 30,00)"),
        (5999, 12, "12x: 1x de R$ 499,99 + 11x de R$ 499,91 (total R$ 5.999,00)"),
    ],
)
def test_installment_plans_describe_every_value_the_customer_will_pay(
    amount: int, count: int, text: str
) -> None:
    """The text lists the first installment separately when it differs, so no cent is hidden."""
    option = next(o for o in installment_options(amount) if o.count == count)
    assert option.describe() == text


def test_quantity_multiplies_the_unit_prices_and_drives_the_totals() -> None:
    """Two Takamine GD20: unit prices stay visible, totals are unit price x quantity."""
    options = payment_options(2199, None, quantity=2)
    assert options.quantity == 2
    assert options.price.final_price == Decimal("2199.00")  # unit
    assert options.total == Decimal("4398.00")
    assert options.pix_price == Decimal("2089.05")  # unit
    assert options.pix_total == Decimal("4178.10")
    assert options.installments[-1].total == Decimal("4398.00")


def test_combined_payment_uses_the_total_with_the_quantity() -> None:
    """Five Ohana CK-20 cost R$ 2.196,00 (> R$ 2.000) although one costs only R$ 439,20."""
    options = payment_options(549, 20, quantity=5)
    assert options.total == Decimal("2196.00")
    assert options.combined_payment_allowed
    assert options.pix_total is None  # promotion: still no PIX discount
    assert not payment_options(549, 20, quantity=1).combined_payment_allowed


def test_quantity_defaults_to_one() -> None:
    """Without a quantity the totals equal the unit prices."""
    options = payment_options(2199, None)
    assert options.quantity == 1
    assert options.total == options.price.final_price
    assert options.pix_total == options.pix_price


@pytest.mark.parametrize("quantity", [0, -1, 1.5])
def test_quantity_must_be_a_positive_integer(quantity: float) -> None:
    """A zero, negative or fractional quantity is rejected instead of silently priced."""
    with pytest.raises(ValueError, match="quantity must be"):
        payment_options(2199, None, quantity=quantity)  # type: ignore[arg-type]
