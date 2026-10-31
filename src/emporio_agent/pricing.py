"""Price, PIX and installment rules from the store policy manual.

Everything here is pure arithmetic on ``Decimal`` (never ``float``), so results are exact, testable
and independent of the LLM. Amounts are in Brazilian reais, rounded half-up to the cent.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

Number = int | float | str | Decimal

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)

# Policy section 3: PIX is 5% off the table price.
PIX_DISCOUNT_PERCENT = Decimal(5)

# Policy section 3.1: credit card, interest-free, up to 12x.
MAX_INSTALLMENTS = 12

# Policy section 3.1: combining payment methods needs a purchase *above* this amount.
COMBINED_PAYMENT_THRESHOLD = Decimal(2000)


def _decimal(value: Number) -> Decimal:
    """Convert a number to ``Decimal`` through its text form (``599.9`` stays ``599.9``)."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    """Round to the cent, half-up."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PriceQuote:
    """Price of one product, with the promotion that produced it (if any).

    Attributes:
        list_price: Table price.
        promo_percent: Active promotion percent, or ``None`` when there is no promotion.
        final_price: Price the customer pays before any payment-method discount.
    """

    list_price: Decimal
    promo_percent: Decimal | None
    final_price: Decimal


def price_product(list_price: Number, promo_percent: Number | None) -> PriceQuote:
    """Apply an active promotion to a table price.

    Args:
        list_price: Table price in reais.
        promo_percent: Active promotion percent, or ``None``.

    Returns:
        The quote with list, promotion and final prices.
    """
    list_value = _decimal(list_price)
    if list_value < 0:
        raise ValueError(f"list_price must be >= 0, got {list_value}")
    if promo_percent is None:
        return PriceQuote(_money(list_value), None, _money(list_value))
    percent = _decimal(promo_percent)
    if not 0 <= percent <= 100:
        raise ValueError(f"promo_percent must be between 0 and 100, got {percent}")
    final = list_value * (_HUNDRED - percent) / _HUNDRED
    return PriceQuote(_money(list_value), percent, _money(final))


def pix_price(list_price: Number, promo_percent: Number | None) -> Decimal | None:
    """Price to pay with PIX: 5% off the table price, never stacked on a promotion.

    Args:
        list_price: Table price in reais.
        promo_percent: Active promotion percent, or ``None``.

    Returns:
        The PIX price, or ``None`` when the product is already on promotion (policy 6.2).
    """
    quote = price_product(list_price, promo_percent)
    if quote.promo_percent is not None:
        return None
    return _money(quote.list_price * (_HUNDRED - PIX_DISCOUNT_PERCENT) / _HUNDRED)


@dataclass(frozen=True)
class InstallmentOption:
    """One way of splitting a credit card payment.

    Cents that do not divide evenly go to the first installment, as the real store does, so the
    installments always add up to the total exactly.

    Attributes:
        count: Number of installments.
        first_value: Value of the first installment (the largest one).
        value: Value of each of the other installments (``count - 1`` of them).
    """

    count: int
    first_value: Decimal
    value: Decimal

    @property
    def total(self) -> Decimal:
        """Sum of all installments, equal to the amount that was split."""
        return self.first_value + self.value * (self.count - 1)

    def describe(self) -> str:
        """Write the plan with every value the customer will pay, e.g. for the tool output.

        Returns:
            ``"10x de R$ 100,00 (total R$ 1.000,00)"`` when all installments are equal, or
            ``"3x: 1x de R$ 333,34 + 2x de R$ 333,33 (total R$ 1.000,00)"`` when the first differs.
        """
        total = f"(total {format_brl(self.total)})"
        if self.first_value == self.value:
            return f"{self.count}x de {format_brl(self.value)} {total}"
        first = f"1x de {format_brl(self.first_value)}"
        others = f"{self.count - 1}x de {format_brl(self.value)}"
        return f"{self.count}x: {first} + {others} {total}"


def min_installment_value(count: int) -> Decimal:
    """Minimum value of each installment for a given number of installments (policy 3.1).

    Up to 3x is R$ 50 (our reading of an ambiguous rule: "no minimum, except below R$ 50"), 4x to 6x
    is R$ 80 and 7x to 12x is R$ 100. A single payment (1x) has no minimum.

    Args:
        count: Number of installments, from 1 to 12.

    Returns:
        The minimum installment value in reais.
    """
    if count <= 1:
        return Decimal(0)
    if count <= 3:
        return Decimal(50)
    if count <= 6:
        return Decimal(80)
    return Decimal(100)


def installment_options(amount: Number) -> list[InstallmentOption]:
    """List every valid installment plan for an amount, from 1x up to the allowed maximum.

    Args:
        amount: Amount to pay on the credit card, in reais.

    Returns:
        Options ordered by installment count; always includes 1x.
    """
    total = _money(_decimal(amount))
    options = []
    for count in range(1, MAX_INSTALLMENTS + 1):
        regular = (total / count).quantize(_CENT, rounding=ROUND_DOWN)
        if regular < min_installment_value(count):
            continue
        options.append(InstallmentOption(count, total - regular * (count - 1), regular))
    return options


def combined_payment_allowed(amount: Number) -> bool:
    """Tell whether the purchase may be paid with more than one method (policy 3.1).

    Args:
        amount: Purchase total in reais.

    Returns:
        ``True`` only for amounts strictly above R$ 2.000,00.
    """
    return _decimal(amount) > COMBINED_PAYMENT_THRESHOLD


def format_brl(value: Number) -> str:
    """Format an amount the Brazilian way, e.g. ``R$ 2.199,00``.

    Args:
        value: Amount in reais.

    Returns:
        Text with a dot for thousands, a comma for cents and always two decimals.
    """
    amount = _money(_decimal(value))
    american = f"{amount:,.2f}"
    return "R$ " + american.replace(",", "_").replace(".", ",").replace("_", ".")


@dataclass(frozen=True)
class PaymentOptions:
    """Everything a customer can choose when paying for ``quantity`` units of one product.

    Unit prices stay visible (the customer sees them in the catalog) and the totals are exactly
    unit price x quantity, so the numbers shown never disagree with each other.

    Attributes:
        price: List, promotion and final price of one unit.
        quantity: Number of units.
        total: Final price of all units.
        pix_price: PIX price of one unit, or ``None`` when the product is on promotion.
        pix_total: PIX price of all units, or ``None`` when the product is on promotion.
        installments: Valid credit card plans over ``total``.
        combined_payment_allowed: Whether more than one payment method may be combined (``total``).
    """

    price: PriceQuote
    quantity: int
    total: Decimal
    pix_price: Decimal | None
    pix_total: Decimal | None
    installments: list[InstallmentOption]
    combined_payment_allowed: bool


def payment_options(
    list_price: Number, promo_percent: Number | None, quantity: int = 1
) -> PaymentOptions:
    """Compute the price and every payment option for ``quantity`` units of a product.

    PIX eligibility depends on the product's own promotion, and the combined-payment rule uses the
    total with the quantity. Carts with several different products are out of scope: price each
    product separately.

    Args:
        list_price: Table price of one unit, in reais.
        promo_percent: Active promotion percent of the product, or ``None``.
        quantity: Number of units, at least 1.

    Returns:
        Unit prices, totals, PIX, installment plans and combined-payment permission.

    Raises:
        ValueError: If ``quantity`` is not a positive integer.
    """
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError(f"quantity must be a positive integer, got {quantity!r}")
    price = price_product(list_price, promo_percent)
    unit_pix = pix_price(list_price, promo_percent)
    total = price.final_price * quantity
    return PaymentOptions(
        price=price,
        quantity=quantity,
        total=total,
        pix_price=unit_pix,
        pix_total=None if unit_pix is None else unit_pix * quantity,
        installments=installment_options(total),
        combined_payment_allowed=combined_payment_allowed(total),
    )
