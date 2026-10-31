"""LangChain tools: the agent's only access to store data.

Arguments are Pydantic models with enums, so the model cannot invent a category. Tool descriptions
are read by the LLM to decide *when* to call each tool, so they say what the tool is for and when
not to use it.
"""

import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from emporio_agent.catalog import Catalog
from emporio_agent.orders import Orders

STORE_TIMEZONE = ZoneInfo("America/Campo_Grande")

# Policy 7.3: complaints are answered within 24 business hours.
ESCALATION_RETURN_WINDOW = "24 horas úteis"


class Category(StrEnum):
    """The nine store categories (names as stored in the database)."""

    GUITARS = "Guitarras"
    BASSES = "Baixos"
    DRUMS = "Baterias e Percussão"
    KEYBOARDS = "Teclados e Pianos"
    ACOUSTIC_GUITARS = "Violões"
    WOODWINDS = "Instrumentos de Sopro (Madeiras)"
    BRASS = "Instrumentos de Sopro (Metais)"
    ORCHESTRAL_STRINGS = "Cordas Orquestrais"
    UKULELES = "Ukuleles"


class StringType(StrEnum):
    """String material of acoustic guitars and ukuleles."""

    NYLON = "nylon"
    STEEL = "steel"


class SearchProductsArgs(BaseModel):
    """Arguments of ``search_products``."""

    category: Category | None = Field(default=None, description="Store category to search in.")
    min_price: float | None = Field(
        default=None, description="Lowest price in reais (promotion included)."
    )
    max_price: float | None = Field(
        default=None, description="Highest price in reais the customer accepts (promo included)."
    )
    string_type: StringType | None = Field(
        default=None, description="String material: nylon (classical) or steel (folk)."
    )
    has_electronics: bool | None = Field(
        default=None, description="True for electro-acoustic instruments (with pickup)."
    )
    keyword: str | None = Field(
        default=None, description="Words from the product name, e.g. a brand or model."
    )


class ProductNameArgs(BaseModel):
    """Arguments of the tools that look a product up by name."""

    product_name: str = Field(description="Product name as the customer wrote it (approximate).")


class PaymentOptionsArgs(BaseModel):
    """Arguments of ``calculate_payment_options``."""

    product_name: str = Field(description="Product name as the customer wrote it (approximate).")
    quantity: int = Field(default=1, ge=1, description="Number of units of that product.")


class EscalationArgs(BaseModel):
    """Arguments of ``escalate_to_human``."""

    reason: str = Field(description="Short reason, e.g. complaint or manager_required.")
    summary: str = Field(description="What happened, with the order number if there is one.")


class NoArgs(BaseModel):
    """Arguments of tools that take none."""


class OrderStatusArgs(BaseModel):
    """Arguments of ``get_order_status``."""

    order_id: int = Field(description="Order number given by the customer.")
    email_or_phone: str = Field(
        description="E-mail or phone from the customer's registration, to prove ownership."
    )


def build_tools(
    db_path: Path,
    escalations_path: Path,
    now: Callable[[], datetime] | None = None,
) -> list[BaseTool]:
    """Create the agent tools bound to a database and an escalation log.

    Args:
        db_path: SQLite file built by ``data_loader.build_database``.
        escalations_path: JSON Lines file where human escalations are appended.
        now: Clock used to stamp escalations (store time zone by default); tests inject one.

    Returns:
        The tools to hand to the agent.
    """
    catalog = Catalog(db_path)
    orders = Orders(db_path)

    def search_products(
        category: Category | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        string_type: StringType | None = None,
        has_electronics: bool | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        return catalog.search(
            category=None if category is None else category.value,
            min_price=min_price,
            max_price=max_price,
            string_type=None if string_type is None else string_type.value,
            has_electronics=has_electronics,
            keyword=keyword,
        )

    def get_product_details(product_name: str) -> dict[str, Any]:
        return catalog.details(product_name)

    def get_order_status(order_id: int, email_or_phone: str) -> dict[str, Any]:
        return orders.status(order_id, email_or_phone)

    def calculate_payment_options(product_name: str, quantity: int = 1) -> dict[str, Any]:
        return catalog.payment(product_name, quantity)

    def escalate_to_human(reason: str, summary: str) -> dict[str, Any]:
        moment = (now or (lambda: datetime.now(STORE_TIMEZONE)))()
        record = {"created_at": moment.isoformat(), "reason": reason, "summary": summary}
        escalations_path.parent.mkdir(parents=True, exist_ok=True)
        with escalations_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "registered": True,
            "return_window": ESCALATION_RETURN_WINDOW,
            "message": (
                "Case registered for the team. Tell the customer someone will get back to them "
                f"within {ESCALATION_RETURN_WINDOW}."
            ),
        }

    def list_active_promotions() -> dict[str, Any]:
        return catalog.active_promotions()

    return [
        StructuredTool.from_function(
            func=escalate_to_human,
            name="escalate_to_human",
            description=(
                "Hand the case to a person: complaints, anything the policy says needs a manager, "
                "or a question you cannot answer from the policy. Registers it and returns the "
                "response window to tell the customer. Do not use it for questions you can answer."
            ),
            args_schema=EscalationArgs,
        ),
        StructuredTool.from_function(
            func=calculate_payment_options,
            name="calculate_payment_options",
            description=(
                "Calculate how to pay for ONE product (and a quantity): price, PIX price, every "
                "installment plan and whether payment methods can be combined. NEVER do this math "
                "yourself. For several different products, call it once per product and tell the "
                "customer that the cart is closed by the store team."
            ),
            args_schema=PaymentOptionsArgs,
        ),
        StructuredTool.from_function(
            func=list_active_promotions,
            name="list_active_promotions",
            description=(
                "List the promotions running right now (original price, discount and final "
                "price). Use for 'what is on sale?' or to check if a promotion still exists. "
                "Never offer a promotion that is not in this list."
            ),
            args_schema=NoArgs,
        ),
        StructuredTool.from_function(
            func=get_order_status,
            name="get_order_status",
            description=(
                "Look up an order: status, items, total, payment, tracking code and delivery "
                "estimate. ALWAYS ask the customer for the order number AND the e-mail or phone on "
                "their registration first; details are only returned if they match the order's "
                "owner. Never guess or reuse another customer's data."
            ),
            args_schema=OrderStatusArgs,
        ),
        StructuredTool.from_function(
            func=get_product_details,
            name="get_product_details",
            description=(
                "Look up ONE product by name: price, promotion, PIX price, availability, specs and "
                "description. Use for 'how much is the Takamine GD20?' or 'is X in stock?'. Works "
                "even for products that are out of stock or discontinued."
            ),
            args_schema=ProductNameArgs,
        ),
        StructuredTool.from_function(
            func=search_products,
            name="search_products",
            description=(
                "Search the catalog for products that are available to buy right now, cheapest "
                "first. Use for questions like 'which acoustic guitars up to R$1000?'. Prices "
                "already include active promotions. Returns at most 8 products and the total."
            ),
            args_schema=SearchProductsArgs,
        ),
    ]
