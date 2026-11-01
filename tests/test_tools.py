"""Behavior tests for the agent tools, exercised through their public LangChain interface."""

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import ValidationError

from emporio_agent.data_loader import build_database, load_clean_tables
from emporio_agent.tools import Category, build_tools

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

Call = Callable[..., dict[str, Any]]


def _brl(text: str) -> Decimal:
    """Parse a customer-facing amount such as ``R$ 1.967,18`` back to a Decimal."""
    return Decimal(text.removeprefix("R$").strip().replace(".", "").replace(",", "."))


@pytest.fixture(scope="module")
def call(tmp_path_factory: pytest.TempPathFactory) -> Call:
    """Return ``call(tool_name, **args)`` over tools built on the real (cleaned) store data."""
    workdir = tmp_path_factory.mktemp("tools")
    db_path = workdir / "emporio.db"
    build_database(DATA_DIR, db_path)
    tools = {tool.name: tool for tool in build_tools(db_path, workdir / "escalations.jsonl")}

    def invoke(name: str, **args: Any) -> dict[str, Any]:
        return tools[name].invoke(args)

    return invoke


def test_search_returns_only_what_fits_the_budget_cheapest_first(call: Call) -> None:
    """'Violões até R$ 1000': 12 sellable matches, the 8 cheapest shown, all within budget."""
    result = call("search_products", category="Violões", max_price=1000)
    assert result["total_matches"] == 12
    assert result["shown"] == 8
    prices = [_brl(product["final_price"]) for product in result["products"]]
    assert prices == sorted(prices)
    assert all(price <= 1000 for price in prices)


def test_price_filters_compare_the_final_price_not_the_list_price(call: Call) -> None:
    """Ohana CK-20 lists at R$ 549 but costs R$ 439,20 with 20% off, so it fits a R$ 500 budget."""
    result = call("search_products", category="Ukuleles", max_price=500)
    ohana = next(p for p in result["products"] if p["name"] == "Ohana CK-20 Concert Natural")
    assert (ohana["list_price"], ohana["discount"], ohana["final_price"]) == (
        "R$ 549,00",
        "20%",
        "R$ 439,20",
    )
    expensive = call("search_products", min_price=5000)
    assert expensive["total_matches"] > 0
    assert all(_brl(p["final_price"]) >= 5000 for p in expensive["products"])


def test_search_by_string_type_electronics_and_accent_free_keyword(call: Call) -> None:
    """Steel-string guitars with electronics match the data exactly; 'aco' finds 'Aço'."""
    products = load_clean_tables(DATA_DIR)["products"]
    expected = products[
        products["is_sellable"]
        & (products["category_id"] == 5)
        & (products["string_type"] == "steel")
        & (products["has_electronics"] == True)  # noqa: E712  (nullable boolean column)
    ]
    result = call("search_products", category="Violões", string_type="steel", has_electronics=True)
    assert result["total_matches"] == len(expected) > 0
    assert {p["name"] for p in result["products"]} <= set(expected["name"])

    by_keyword = call("search_products", keyword="aco folk")
    assert by_keyword["total_matches"] > 0
    assert all(
        "aço" in p["name"].lower() and "folk" in p["name"].lower() for p in by_keyword["products"]
    )


@pytest.mark.parametrize(
    "keyword",
    [
        "giannini gf-3d",  # active but out of stock
        "shelby sn-7c",  # discontinued, no stock
        "ibanez bass 2x",  # discontinued WITH stock
        "kala ka-abp-ctg",  # coming soon WITH stock
    ],
)
def test_products_that_cannot_be_sold_never_show_up_in_searches(call: Call, keyword: str) -> None:
    """Out of stock, discontinued and coming-soon products are hidden even if stock is positive."""
    assert call("search_products", keyword=keyword)["total_matches"] == 0
    assert call("search_products")["total_matches"] == 61


def test_empty_categories_say_so_instead_of_inventing_products(call: Call) -> None:
    """Woodwinds exist as a category but the store has no such product right now."""
    result = call("search_products", category="Instrumentos de Sopro (Madeiras)")
    assert result["total_matches"] == 0
    assert result["products"] == []
    assert "no products" in result["note"]
    assert "note" not in call("search_products", category="Violões")


def test_details_find_a_product_by_an_approximate_name(call: Call) -> None:
    """'Takamine GD20': R$ 2.199, no promotion, PIX discount available, in stock."""
    result = call("get_product_details", product_name="Takamine GD20")
    assert result["found"] is True
    assert result["name"] == "Takamine GD20 Dreadnought Natural"
    assert result["category"] == "Violões"
    assert result["final_price"] == "R$ 2.199,00"
    assert result["discount"] is None
    assert result["pix_price"] == "R$ 2.089,05"
    assert result["availability"] == "Disponível"
    assert result["specs"]["string_type"] in {"nylon", "steel"}


def test_details_of_a_promotional_product_have_no_pix_discount(call: Call) -> None:
    """Ohana CK-20: shows list price, percent and final price; PIX does not stack (policy 6.2)."""
    for typed in ("ohana ck 20", "OHANA CK20", "Ohana CK-20"):
        assert call("get_product_details", product_name=typed)["name"] == (
            "Ohana CK-20 Concert Natural"
        )
    result = call("get_product_details", product_name="ohana ck 20")
    assert result["list_price"] == "R$ 549,00"
    assert result["discount"] == "20%"
    assert result["final_price"] == "R$ 439,20"
    assert result["pix_price"] is None
    assert "promotion" in result["pix_note"]


def test_unavailable_products_come_with_the_closest_available_alternatives(call: Call) -> None:
    """Giannini GF-3D is out of stock: suggest 3 sellable guitars, similar and close in price."""
    products = load_clean_tables(DATA_DIR)["products"].set_index("product_id")
    target = products.loc[96]
    result = call("get_product_details", product_name="Giannini GF-3D")
    assert result["found"] is True
    assert result["availability"].startswith("Esgotado")
    alternatives = result["alternatives"]
    assert len(alternatives) == 3
    names = {p["name"] for p in alternatives}
    assert target["name"] not in names

    sellable_same_category = products[
        products["is_sellable"] & (products["category_id"] == target["category_id"])
    ]
    assert names <= set(sellable_same_category["name"])

    def distance(row: Any) -> tuple[bool, bool, float]:
        promo = 0 if pd.isna(row["active_promo_percent"]) else row["active_promo_percent"]
        price = row["price_brl"] * (1 - promo / 100)
        return (
            row["string_type"] != target["string_type"],
            row["has_electronics"] != target["has_electronics"],
            abs(price - float(target["price_brl"])),
        )

    best = sorted(sellable_same_category.itertuples(), key=lambda r: distance(r._asdict()))[:3]
    assert names == {row.name for row in best}


@pytest.mark.parametrize(
    ("typed", "availability_prefix"),
    [("Ibanez Bass 2X", "Descontinuado"), ("Kala KA-ABP-CTG", "Em breve")],
)
def test_discontinued_and_coming_soon_products_are_never_offered_as_available(
    call: Call, typed: str, availability_prefix: str
) -> None:
    """They have stock in the data but the agent must say they cannot be bought."""
    result = call("get_product_details", product_name=typed)
    assert result["availability"].startswith(availability_prefix)
    assert result["alternatives"]


def test_contradictory_descriptions_are_not_returned(call: Call) -> None:
    """Product 135's description names another brand, so only structured data is returned."""
    unreliable = call("get_product_details", product_name="Music Man Bass 1X")
    assert unreliable["description"] is None
    assert unreliable["specs"]["string_count"] == 4
    assert call("get_product_details", product_name="Takamine GD20")["description"]
    assert "alternatives" not in call("get_product_details", product_name="Takamine GD20")


def test_an_ambiguous_name_returns_candidates_instead_of_guessing(call: Call) -> None:
    """'Yamaha' alone fits many products: the agent must ask which one, not pick a random price."""
    result = call("get_product_details", product_name="Yamaha")
    assert result["found"] is False
    assert result["ambiguous"] is True
    assert 2 <= len(result["candidates"]) <= 5
    assert all("Yamaha" in name for name in result["candidates"])
    assert "final_price" not in result


@pytest.mark.parametrize("typed", ["Saxofone Yamaha YAS-280", "Violino", "xyzzy"])
def test_an_unknown_product_is_reported_as_not_found(call: Call, typed: str) -> None:
    """Nothing is invented for products the store does not have."""
    result = call("get_product_details", product_name=typed)
    assert result["found"] is False
    assert "ambiguous" not in result
    assert "search_products" in result["note"]


def _customer_of(order_id: int) -> Any:
    """Return the cleaned customer row that owns an order (test oracle)."""
    tables = load_clean_tables(DATA_DIR)
    customer_id = tables["orders"].set_index("order_id").loc[order_id, "customer_id"]
    return tables["customers"].set_index("customer_id").loc[customer_id]


def test_a_verified_customer_sees_the_order_status(call: Call) -> None:
    """Order 8 (shipped): status, tracking code, delivery estimate, items and total."""
    owner = _customer_of(8)
    result = call("get_order_status", order_id=8, email_or_phone=owner["email"])
    assert result["verified"] is True
    assert result["status"] == "Enviado"
    assert result["payment_method"] == "Cartão de débito"
    assert result["total"] == "R$ 349,90"
    assert result["tracking_code"] == "BRJL5544332BR"
    assert result["estimated_delivery"] == "28/02/2026"
    assert result["order_date"] == "15/02/2026"
    assert len(result["items"]) == 1 and result["items"][0]["quantity"] == 1
    assert result["customer_first_name"] == owner["name"].split()[0]


def test_orders_not_shipped_yet_have_no_tracking_code(call: Call) -> None:
    """A confirmed order has no tracking code or delivery date, and the tool says so."""
    result = call("get_order_status", order_id=9, email_or_phone=_customer_of(9)["email"])
    assert result["verified"] is True
    assert result["status"] == "Confirmado"
    assert result["tracking_code"] is None
    assert result["estimated_delivery"] is None
    assert "tracking_note" in result
    cancelled = call("get_order_status", order_id=17, email_or_phone=_customer_of(17)["email"])
    assert cancelled["status"] == "Cancelado"
    assert cancelled["notes"] == "Pedido cancelado pelo cliente antes do processamento"


def test_failed_checks_look_the_same_whether_or_not_the_order_exists(call: Call) -> None:
    """No hint about which orders exist: wrong contact and unknown order answer identically."""
    stranger = _customer_of(1)["email"]  # a real customer, but not the owner of order 8
    wrong_owner = call("get_order_status", order_id=8, email_or_phone=stranger)
    unknown_order = call("get_order_status", order_id=999, email_or_phone=stranger)
    assert wrong_owner == unknown_order
    assert wrong_owner["verified"] is False
    assert set(wrong_owner) == {"verified", "message"}
    for junk in ("", "abc", "12345"):
        assert call("get_order_status", order_id=8, email_or_phone=junk) == wrong_owner


@pytest.mark.parametrize(
    "typed",
    ["(67) 99654-7821", "67996547821", "+55 67 99654-7821", "  ANACAROL.Ferreira@coldmail.com "],
)
def test_phone_and_email_are_normalized_before_comparing(call: Call, typed: str) -> None:
    """Formatting, country code, spaces and letter case do not matter (order 8, customer 2)."""
    result = call("get_order_status", order_id=8, email_or_phone=typed)
    assert result["verified"] is True


def test_a_shared_email_alone_never_unlocks_an_order(call: Call) -> None:
    """Customers 11 and 43 share an e-mail: order 10 needs the owner's phone, not that e-mail."""
    owner = _customer_of(10)
    assert owner["email_is_shared"]
    by_email = call("get_order_status", order_id=10, email_or_phone=owner["email"])
    assert by_email["verified"] is False
    assert by_email["needs_phone"] is True
    assert set(by_email) == {"verified", "needs_phone", "message"}

    by_phone = call("get_order_status", order_id=10, email_or_phone=owner["phone"])
    assert by_phone["verified"] is True
    assert by_phone["order_id"] == 10

    other = load_clean_tables(DATA_DIR)["customers"].set_index("customer_id").loc[43]
    assert other["email"] == owner["email"]  # same e-mail, different person
    impostor = call("get_order_status", order_id=10, email_or_phone=other["phone"])
    assert impostor == call("get_order_status", order_id=999, email_or_phone=other["phone"])
    assert impostor["verified"] is False


def test_only_active_promotions_are_listed_with_original_percent_and_final_price(
    call: Call,
) -> None:
    """The 4 active promotions, biggest discount first, never an expired one (policy 6.2 / 7.3)."""
    result = call("list_active_promotions")
    assert result["total"] == 4
    listed = [
        (p["name"], p["list_price"], p["discount"], p["final_price"]) for p in result["promotions"]
    ]
    assert listed == [
        ("Ohana CK-20 Concert Natural", "R$ 549,00", "20%", "R$ 439,20"),
        ("Crafter HT-100 Folk Aço Natural", "R$ 2.399,00", "18%", "R$ 1.967,18"),
        ("Kalani KAL-700T Tenor Natural", "R$ 359,00", "10%", "R$ 323,10"),
        ("Taylor 110e Dreadnought Natural", "R$ 5.999,00", "8%", "R$ 5.519,08"),
    ]
    assert "PIX" in result["note"]


def test_payment_options_for_a_product_without_promotion(call: Call) -> None:
    """Takamine GD20: PIX discount, 12 plans spelled out, combined payment allowed."""
    result = call("calculate_payment_options", product_name="Takamine GD20")
    assert result["found"] is True and result["available"] is True
    assert (result["quantity"], result["unit_price"], result["total"]) == (
        1,
        "R$ 2.199,00",
        "R$ 2.199,00",
    )
    assert result["pix_total"] == "R$ 2.089,05"
    assert result["max_installments"] == 12
    assert len(result["installment_plans"]) == 12
    assert result["installment_plans"][-1] == "12x de R$ 183,25 (total R$ 2.199,00)"
    assert result["combined_payment_allowed"] is True


def test_payment_options_use_the_total_with_the_quantity(call: Call) -> None:
    """Five Ohana CK-20 on promotion: no PIX discount, and R$ 2.196 allows combined payment."""
    result = call("calculate_payment_options", product_name="Ohana CK-20", quantity=5)
    assert (result["unit_price"], result["total"]) == ("R$ 439,20", "R$ 2.196,00")
    assert result["pix_total"] is None
    assert "promotion" in result["pix_note"]
    assert result["combined_payment_allowed"] is True
    assert "3x de R$ 732,00 (total R$ 2.196,00)" in result["installment_plans"]
    one = call("calculate_payment_options", product_name="Ohana CK-20")
    assert one["combined_payment_allowed"] is False
    assert one["max_installments"] == 5


def test_leftover_cents_are_spelled_out_in_the_installment_plans(call: Call) -> None:
    """Taylor 110e (R$ 5.519,08) in 3x: the first installment carries the extra cent."""
    result = call("calculate_payment_options", product_name="Taylor 110e")
    assert (
        "3x: 1x de R$ 1.839,70 + 2x de R$ 1.839,69 (total R$ 5.519,08)"
        in result["installment_plans"]
    )


def test_payment_options_are_not_calculated_for_products_that_cannot_be_bought(
    call: Call,
) -> None:
    """Out of stock: say so and offer alternatives instead of a price to pay."""
    result = call("calculate_payment_options", product_name="Giannini GF-3D")
    assert result["found"] is True and result["available"] is False
    assert "total" not in result and "installment_plans" not in result
    assert result["alternatives"]
    assert call("calculate_payment_options", product_name="Yamaha")["ambiguous"] is True
    assert call("calculate_payment_options", product_name="Violino")["found"] is False


@pytest.mark.parametrize("quantity", [0, -2])
def test_quantity_must_be_at_least_one(call: Call, quantity: int) -> None:
    """The tool schema itself rejects a zero or negative quantity."""
    with pytest.raises(ValidationError):
        call("calculate_payment_options", product_name="Takamine GD20", quantity=quantity)


def test_escalation_is_logged_and_the_customer_gets_the_response_window(tmp_path: Path) -> None:
    """Complaints go to a person: one JSON line per case, in the store time zone, appended."""
    db_path = tmp_path / "emporio.db"
    build_database(DATA_DIR, db_path)
    log = tmp_path / "var" / "escalations.jsonl"
    moment = datetime(2026, 3, 23, 10, 30, tzinfo=ZoneInfo("America/Campo_Grande"))
    tools = {t.name: t for t in build_tools(db_path, log, now=lambda: moment)}

    first = tools["escalate_to_human"].invoke(
        {"reason": "complaint", "summary": "Customer received a damaged guitar, order 5."}
    )
    tools["escalate_to_human"].invoke({"reason": "manager_required", "summary": "Second case."})

    assert first["registered"] is True
    assert first["return_window"] == "24 horas úteis"
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [line["reason"] for line in lines] == ["complaint", "manager_required"]
    assert lines[0]["summary"] == "Customer received a damaged guitar, order 5."
    assert lines[0]["created_at"] == "2026-03-23T10:30:00-04:00"


def test_the_agent_gets_exactly_the_six_documented_tools(tmp_path: Path) -> None:
    """Names are the contract with the prompt; every tool explains itself to the model."""
    db_path = tmp_path / "emporio.db"
    build_database(DATA_DIR, db_path)
    tools = build_tools(db_path, tmp_path / "escalations.jsonl")
    assert {tool.name for tool in tools} == {
        "search_products",
        "get_product_details",
        "get_order_status",
        "list_active_promotions",
        "calculate_payment_options",
        "escalate_to_human",
    }
    assert all(len(tool.description) > 40 for tool in tools)


def test_the_category_enum_matches_the_store_categories_and_rejects_invented_ones(
    call: Call,
) -> None:
    """The model can only filter by the nine real categories, as stored in the data."""
    stored = set(load_clean_tables(DATA_DIR)["categories"]["name"])
    assert {category.value for category in Category} == stored
    with pytest.raises(ValidationError):
        call("search_products", category="Saxofones")


def test_the_escalation_tool_description_lists_every_reason_to_use_it(tmp_path: Path) -> None:
    """The model reads it: complaints, managers, policy doubts, a person, data deletion."""
    tools = {tool.name: tool for tool in build_tools(tmp_path / "x.db", tmp_path / "e.jsonl")}
    text = tools["escalate_to_human"].description.lower()
    for reason in ("complaint", "manager", "doubt", "talk to a person", "deletion"):
        assert reason in text


def test_the_escalation_fields_ask_for_neutral_facts_only(tmp_path: Path) -> None:
    """The record the team reads must not carry a verdict the manager has not made."""
    tools = {tool.name: tool for tool in build_tools(tmp_path / "x.db", tmp_path / "e.jsonl")}
    fields = tools["escalate_to_human"].args_schema.model_fields  # type: ignore[union-attr]
    summary = (fields["summary"].description or "").lower()
    assert "facts only" in summary and "no verdict" in summary
    assert "out of the deadline" in summary  # the forbidden example is named
    assert "receipt date not given" in summary
    reason = (fields["reason"].description or "").lower()
    for code in ("complaint", "manager_required", "policy_doubt", "human_requested"):
        assert code in reason
    assert "data_deletion" in reason
