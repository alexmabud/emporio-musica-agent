"""Behavior tests for the CSV loader that cleans store data (see docs/assumptions.md)."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from emporio_agent.data_loader import DataValidationError, build_database, load_clean_tables

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    """Clean tables built once from the real store CSVs."""
    return load_clean_tables(DATA_DIR)


def _product(tables: dict[str, pd.DataFrame], product_id: int) -> pd.Series:
    """Return one product row by id."""
    products = tables["products"]
    return products.loc[products["product_id"] == product_id].iloc[0]


def test_sellable_requires_active_status_and_stock(tables: dict[str, pd.DataFrame]) -> None:
    """Stock alone is not enough: discontinued and coming-soon items with stock are not sellable."""
    assert _product(tables, 95)["is_sellable"]  # active, in stock
    assert not _product(tables, 96)["is_sellable"]  # active, stock 0
    assert not _product(tables, 136)["is_sellable"]  # discontinued, stock 13
    assert not _product(tables, 130)["is_sellable"]  # coming_soon, stock 2
    assert tables["products"]["is_sellable"].sum() == 61


def test_contradictory_descriptions_are_withheld(tables: dict[str, pd.DataFrame]) -> None:
    """Seven products describe another brand/model than their name or specs: no description kept."""
    contradictory = (135, 136, 137, 139, 140, 142, 144)
    for product_id in contradictory:
        product = _product(tables, product_id)
        assert not product["description_reliable"]
        assert pd.isna(product["description"])
    unreliable = tables["products"].query("not description_reliable")["product_id"].tolist()
    assert unreliable == list(contradictory)
    # 138 (StingRay), 141 (Stage Custom) and 143 (Nord Stage) match their brand and specs.
    for product_id in (131, 132, 133, 134, 138, 141, 143, 145, 95):
        product = _product(tables, product_id)
        assert product["description_reliable"]
        assert product["description"]


def test_truncated_name_is_fixed_and_raw_name_kept(tables: dict[str, pd.DataFrame]) -> None:
    """Product 84 'Cedr' becomes 'Cedro'; the valid English 'Cedar' names stay untouched."""
    fixed = _product(tables, 84)
    assert fixed["name"] == "Giannini GN-15 Nylon Cedro Natural"
    assert fixed["name_raw"] == "Giannini GN-15 Nylon Cedr Natural"
    assert _product(tables, 130)["name"] == "Kala KA-ABP-CTG Barítono Cedar"
    changed = tables["products"].query("name != name_raw")
    assert changed["product_id"].tolist() == [84]


def test_search_name_ignores_accents_and_case(tables: dict[str, pd.DataFrame]) -> None:
    """'Aço' and 'Elétrico' can be found by typing 'aco' and 'eletrico'."""
    assert _product(tables, 86)["name_search"] == "tagima dallas tuner aco natural"
    assert _product(tables, 98)["name_search"] == "yamaha ntx1 eletrico nylon natural"


def test_specs_are_parsed_with_clear_types(tables: dict[str, pd.DataFrame]) -> None:
    """Specs become typed fields; 'strings' is split into a count or a material by category."""
    steel_guitar = _product(tables, 86)  # violão, "Aço"
    assert steel_guitar["string_type"] == "steel"
    assert pd.isna(steel_guitar["string_count"])
    assert not steel_guitar["has_electronics"]
    assert _product(tables, 98)["has_electronics"]
    assert _product(tables, 84)["string_type"] == "nylon"

    electric_guitar = _product(tables, 131)
    assert electric_guitar["string_count"] == 6
    assert pd.isna(electric_guitar["string_type"])
    assert pd.isna(electric_guitar["has_electronics"])

    specs = json.loads(_product(tables, 142)["specs"])
    assert specs["keys"] == 61
    assert specs["polyphony"] == 128
    assert json.loads(electric_guitar["specs"])["string_count"] == 6
    assert "strings" not in json.loads(electric_guitar["specs"])


def test_only_active_promotions_reach_products(tables: dict[str, pd.DataFrame]) -> None:
    """Of the 25 promotion rows only the 4 active ones set a product's promo percent."""
    products = tables["products"].set_index("product_id")
    promo = products["active_promo_percent"].dropna()
    assert promo.to_dict() == {90: 18.0, 94: 8.0, 121: 20.0, 127: 10.0}
    assert pd.isna(products.loc[96, "active_promo_percent"])  # only expired promos (18%, 40%)
    assert len(tables["promotions"]) == 25
    assert tables["promotions"]["is_active"].sum() == 4


def test_categories_report_how_many_products_they_have(tables: dict[str, pd.DataFrame]) -> None:
    """Woodwinds, brass and orchestral strings (6-8) exist but have no products."""
    counts = tables["categories"].set_index("category_id")["product_count"].to_dict()
    assert counts == {1: 5, 2: 4, 3: 3, 4: 3, 5: 35, 6: 0, 7: 0, 8: 0, 9: 15}


def test_customer_contacts_are_normalized_and_shared_email_is_flagged(
    tables: dict[str, pd.DataFrame],
) -> None:
    """Phones keep digits only, e-mails are lowercased, and customers 11 and 43 share an e-mail."""
    customers = tables["customers"].set_index("customer_id")
    assert customers.loc[1, "phone_digits"] == "67998123456"
    assert (customers["phone_digits"].str.len() == 11).all()
    assert customers.loc[1, "email_normalized"] == "lucas.mendes@jmail.com"
    assert customers.index[customers["email_is_shared"]].tolist() == [11, 43]
    assert not customers["phone_digits"].duplicated().any()


def test_order_status_and_payment_are_translated(tables: dict[str, pd.DataFrame]) -> None:
    """Orders carry customer-ready PT-BR labels for status and payment method."""
    orders = tables["orders"].set_index("order_id")
    assert orders.loc[1, ["status_pt", "payment_pt"]].tolist() == ["Entregue", "PIX"]
    assert orders.loc[2, "payment_pt"] == "Cartão de crédito em 6x"
    assert orders.loc[3, "payment_pt"] == "Cartão de crédito em 12x"
    assert orders.loc[4, "payment_pt"] == "Boleto"
    assert orders.loc[8, ["status_pt", "payment_pt"]].tolist() == ["Enviado", "Cartão de débito"]
    assert orders.loc[10, "status_pt"] == "Confirmado"
    assert orders.loc[13, "status_pt"] == "Pendente"
    assert orders.loc[17, "status_pt"] == "Cancelado"
    assert orders["status_pt"].notna().all()
    assert orders["payment_pt"].notna().all()


def _data_copy(tmp_path: Path) -> Path:
    """Copy the real CSVs to a temporary folder so a test can corrupt them safely."""
    target = tmp_path / "data"
    target.mkdir()
    for csv_file in DATA_DIR.glob("*.csv"):
        shutil.copy(csv_file, target / csv_file.name)
    return target


def test_missing_tracking_and_delivery_are_null(tables: dict[str, pd.DataFrame]) -> None:
    """Only shipped/delivered orders have tracking and delivery date; the rest stay null."""
    orders = tables["orders"].set_index("order_id")
    assert orders.loc[8, "tracking_code"] == "BRJL5544332BR"
    assert orders.loc[8, "estimated_delivery"] == "2026-02-28"
    with_tracking = orders.index[orders["tracking_code"].notna()].tolist()
    assert with_tracking == [1, 2, 3, 4, 5, 6, 7, 8]
    assert orders.loc[9:, ["tracking_code", "estimated_delivery"]].isna().all().all()
    assert orders.loc[17, "notes"] == "Pedido cancelado pelo cliente antes do processamento"
    assert pd.isna(orders.loc[1, "notes"])


def test_tracking_code_that_breaks_the_policy_format_is_dropped(tmp_path: Path) -> None:
    """A code not shaped like BR + 9 alphanumerics + BR is never shown to customers."""
    data_dir = _data_copy(tmp_path)
    orders_csv = data_dir / "orders.csv"
    orders_csv.write_text(orders_csv.read_text(encoding="utf-8").replace("BRAB1234567BR", "XX123"))
    orders = load_clean_tables(data_dir)["orders"].set_index("order_id")
    assert pd.isna(orders.loc[1, "tracking_code"])
    assert orders.loc[2, "tracking_code"] == "BRCX9876543BR"


def test_orders_flag_totals_that_disagree_with_their_items(
    tables: dict[str, pd.DataFrame],
) -> None:
    """The stored total stays the source of truth; orders 3 and 20 are flagged as mismatched."""
    orders = tables["orders"].set_index("order_id")
    assert orders.index[orders["total_mismatch"]].tolist() == [3, 20]
    assert orders.loc[3, ["total_brl", "items_total_brl"]].tolist() == [3450.0, 3498.0]
    assert orders.loc[20, ["total_brl", "items_total_brl"]].tolist() == [1400.0, 1488.0]
    assert orders.loc[1, "items_total_brl"] == 11499.0


def test_orders_flag_installments_below_the_policy_minimum(
    tables: dict[str, pd.DataFrame],
) -> None:
    """Orders 9, 16 and 18 were split into installments smaller than policy section 3.1 allows."""
    orders = tables["orders"].set_index("order_id")
    assert orders.index[orders["installment_rule_violation"]].tolist() == [9, 16, 18]
    assert not orders.loc[7, "installment_rule_violation"]  # 3x of R$53.30, above the R$50 floor
    assert not orders.loc[1, "installment_rule_violation"]  # PIX has no installments


def test_orders_flag_items_that_are_not_sellable_today(
    tables: dict[str, pd.DataFrame],
) -> None:
    """Orders 3 and 14 hold a coming-soon product and order 4 one that is out of stock."""
    orders = tables["orders"].set_index("order_id")
    assert orders.index[orders["has_unsellable_item"]].tolist() == [3, 4, 14]


@pytest.mark.parametrize(
    ("csv_name", "original", "broken"),
    [
        ("order_items.csv", "1,1,93\n", "1,1,999\n"),
        ("promotions.csv", "1,102,10,Black Friday,0", "1,999,10,Black Friday,0"),
    ],
)
def test_rows_pointing_to_unknown_products_are_rejected(
    tmp_path: Path, csv_name: str, original: str, broken: str
) -> None:
    """A dangling product reference stops the load instead of producing wrong answers."""
    data_dir = _data_copy(tmp_path)
    csv_path = data_dir / csv_name
    text = csv_path.read_text(encoding="utf-8")
    assert original in text
    csv_path.write_text(text.replace(original, broken, 1), encoding="utf-8")
    with pytest.raises(DataValidationError, match="999"):
        load_clean_tables(data_dir)


@pytest.mark.parametrize(("original", "broken"), [("delivered", "teleported"), (",pix,", ",cash,")])
def test_unknown_order_status_or_payment_method_is_rejected(
    tmp_path: Path, original: str, broken: str
) -> None:
    """A status or payment method without a PT-BR label must not slip through untranslated."""
    data_dir = _data_copy(tmp_path)
    orders_csv = data_dir / "orders.csv"
    text = orders_csv.read_text(encoding="utf-8")
    assert original in text
    orders_csv.write_text(text.replace(original, broken, 1), encoding="utf-8")
    with pytest.raises(DataValidationError, match=broken.strip(",")):
        load_clean_tables(data_dir)


def test_database_is_built_from_the_clean_tables_and_can_be_rebuilt(tmp_path: Path) -> None:
    """The SQLite file holds every clean table, creates its folder and survives a rebuild."""
    db_path = tmp_path / "var" / "emporio.db"
    build_database(DATA_DIR, db_path)
    build_database(DATA_DIR, db_path)  # rebuilding replaces the tables instead of failing

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in rows}
        assert names == {
            "categories",
            "customers",
            "orders",
            "order_items",
            "products",
            "promotions",
        }
        sellable = connection.execute("SELECT COUNT(*) FROM products WHERE is_sellable").fetchone()
        assert sellable == (61,)
        name = connection.execute("SELECT name FROM products WHERE product_id = 84").fetchone()
        assert name == ("Giannini GN-15 Nylon Cedro Natural",)
        orders = connection.execute("SELECT COUNT(*) FROM orders").fetchone()
        assert orders == (20,)


def test_building_the_database_never_modifies_the_original_files(tmp_path: Path) -> None:
    """Cleaning happens in code only: the CSVs and the policy PDF stay byte-identical."""
    originals = sorted(p for p in DATA_DIR.iterdir() if p.is_file())
    assert len(originals) == 7  # 6 CSVs + politicas_da_loja.pdf
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    build_database(DATA_DIR, tmp_path / "emporio.db")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    assert after == before


def test_products_are_sorted_by_id(tables: dict[str, pd.DataFrame]) -> None:
    """All 65 products load, ordered by id (the raw file has 145 before 131)."""
    ids = tables["products"]["product_id"].tolist()
    assert len(ids) == 65
    assert ids == sorted(ids)
