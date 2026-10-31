"""Load the store CSVs, clean them in code and expose them as tidy tables.

The original files in ``data/`` are never modified: every cleaning rule lives here, so the result
is reproducible. Rules and their rationale are listed in ``docs/assumptions.md``.
"""

import json
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

import pandas as pd

_TABLES = ("categories", "customers", "orders", "order_items", "products", "promotions")

# Products whose description names another brand/model than the name and specs (audited by hand).
_UNRELIABLE_DESCRIPTION_IDS = frozenset(range(135, 145))

_STATUS_PT = {
    "delivered": "Entregue",
    "shipped": "Enviado",
    "confirmed": "Confirmado",
    "pending": "Pendente",
    "cancelled": "Cancelado",
}
# Tracking code format from the policy manual (section 5.3): BR + 9 alphanumerics + BR.
_TRACKING_CODE = r"BR[A-Z0-9]{9}BR"
_PAYMENT_PT = {"pix": "PIX", "debit": "Cartão de débito", "boleto": "Boleto"}

# Typos in product names, confirmed against the product's own specs (product 84 has top "Cedro").
_NAME_FIXES = {84: "Giannini GN-15 Nylon Cedro Natural"}


class DataValidationError(ValueError):
    """Raised when the source CSVs break a rule the rest of the system relies on."""


# (table, column, referenced table, referenced column)
_REFERENCES = (
    ("products", "category_id", "categories", "category_id"),
    ("promotions", "product_id", "products", "product_id"),
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
)


def _check_references(tables: dict[str, pd.DataFrame]) -> None:
    """Fail loudly if any foreign key points to a row that does not exist."""
    for table, column, target, target_column in _REFERENCES:
        dangling = set(tables[table][column]) - set(tables[target][target_column])
        if dangling:
            raise DataValidationError(
                f"{table}.{column} references unknown {target}.{target_column}: {sorted(dangling)}"
            )


def _read_csv(data_dir: Path, name: str) -> pd.DataFrame:
    """Read one CSV as text so that no value is silently coerced or turned into NaN."""
    return pd.read_csv(data_dir / f"{name}.csv", dtype=str, keep_default_na=False)


def _fold(text: str) -> str:
    """Lowercase ``text`` and strip accents so that 'Aço' matches 'aco'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _payment_label(method: str) -> str | None:
    """Translate a raw payment method (``credit_6x``, ``pix``...) to a PT-BR label."""
    if method.startswith("credit_"):
        return f"Cartão de crédito em {method.removeprefix('credit_')}"
    return _PAYMENT_PT.get(method)


def _breaks_installment_rule(method: str, total: float) -> bool:
    """Tell whether a credit order has installments below the policy minimum (section 3.1).

    Minimum per installment: R$50 up to 3x (our reading of an ambiguous rule), R$80 for 4-6x
    and R$100 for 7-12x. Non-credit methods have no installments.
    """
    if not method.startswith("credit_"):
        return False
    count = int(method.removeprefix("credit_").removesuffix("x"))
    minimum = 50 if count <= 3 else 80 if count <= 6 else 100
    return total / count < minimum


def _clean_specs(raw: str) -> dict[str, object]:
    """Parse a raw specs JSON string into typed fields.

    The raw ``strings`` key means a count for guitars/basses ("6") and a material for acoustic
    guitars/ukuleles ("nylon"/"steel"), so it is split into ``string_count`` / ``string_type``.
    ``electronics`` ("yes"/"no") becomes the boolean ``has_electronics``. Other numeric strings
    become integers.
    """
    specs: dict[str, object] = {}
    for key, value in json.loads(raw).items():
        if key == "strings":
            specs["string_count" if str(value).isdigit() else "string_type"] = (
                int(value) if str(value).isdigit() else value
            )
        elif key == "electronics":
            specs["has_electronics"] = value == "yes"
        elif isinstance(value, str) and value.isdigit():
            specs[key] = int(value)
        else:
            specs[key] = value
    return specs


def _clean_promotions(promotions: pd.DataFrame) -> pd.DataFrame:
    """Type the promotions table; ``is_active`` becomes a boolean."""
    promotions["promotion_id"] = promotions["promotion_id"].astype(int)
    promotions["product_id"] = promotions["product_id"].astype(int)
    promotions["discount_percent"] = promotions["discount_percent"].astype(float)
    promotions["is_active"] = promotions["is_active"] == "1"
    return promotions


def _clean_products(products: pd.DataFrame, promotions: pd.DataFrame) -> pd.DataFrame:
    """Type, fix and enrich products (sellability, reliable description, parsed specs, promo)."""
    products["product_id"] = products["product_id"].astype(int)
    products["category_id"] = products["category_id"].astype(int)
    products["price_brl"] = products["price_brl"].astype(float)
    products["stock_quantity"] = products["stock_quantity"].astype(int)

    products["name_raw"] = products["name"]
    products["name"] = products["product_id"].map(_NAME_FIXES).fillna(products["name_raw"])
    products["name_search"] = products["name"].map(_fold)

    products["is_sellable"] = (products["status"] == "active") & (products["stock_quantity"] > 0)
    products["description_reliable"] = ~products["product_id"].isin(_UNRELIABLE_DESCRIPTION_IDS)
    products["description"] = products["description"].where(products["description_reliable"], None)

    parsed = products["specs"].map(_clean_specs)
    products["specs"] = parsed.map(lambda specs: json.dumps(specs, ensure_ascii=False))
    products["string_type"] = parsed.map(lambda specs: specs.get("string_type"))
    products["string_count"] = pd.array(
        parsed.map(lambda specs: specs.get("string_count")), dtype="Int64"
    )
    products["has_electronics"] = pd.array(
        parsed.map(lambda specs: specs.get("has_electronics")), dtype="boolean"
    )

    active = promotions[promotions["is_active"]].set_index("product_id")["discount_percent"]
    products["active_promo_percent"] = products["product_id"].map(active)
    return products.sort_values("product_id").reset_index(drop=True)


def _clean_customers(customers: pd.DataFrame) -> pd.DataFrame:
    """Normalize contacts and flag e-mails shared by more than one customer."""
    customers["customer_id"] = customers["customer_id"].astype(int)
    customers["phone_digits"] = customers["phone"].str.replace(r"\D", "", regex=True)
    customers["email_normalized"] = customers["email"].str.strip().str.lower()
    customers["email_is_shared"] = customers["email_normalized"].duplicated(keep=False)
    return customers


def _clean_order_items(items: pd.DataFrame) -> pd.DataFrame:
    """Type the order items table."""
    for column in ("order_id", "product_id", "quantity"):
        items[column] = items[column].astype(int)
    return items


def _clean_orders(
    orders: pd.DataFrame, items: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    """Translate, validate and annotate orders; the stored total stays the source of truth."""
    orders["order_id"] = orders["order_id"].astype(int)
    orders["customer_id"] = orders["customer_id"].astype(int)
    orders["total_brl"] = orders["total_brl"].astype(float)

    orders["status_pt"] = orders["status"].map(_STATUS_PT)
    orders["payment_pt"] = orders["payment_method"].map(_payment_label)
    for source, label in (("status", "status_pt"), ("payment_method", "payment_pt")):
        unknown = sorted(set(orders.loc[orders[label].isna(), source]))
        if unknown:
            raise DataValidationError(f"orders.{source} has values without a label: {unknown}")

    by_product = products.set_index("product_id")
    unit_price = items["product_id"].map(by_product["price_brl"])
    items_total = (items["quantity"] * unit_price).groupby(items["order_id"]).sum()
    orders["items_total_brl"] = orders["order_id"].map(items_total)
    orders["total_mismatch"] = (orders["total_brl"] - orders["items_total_brl"]).abs() > 0.01

    item_sellable = items["product_id"].map(by_product["is_sellable"])
    unsellable_orders = set(items.loc[~item_sellable, "order_id"])
    orders["has_unsellable_item"] = orders["order_id"].isin(unsellable_orders)
    orders["installment_rule_violation"] = [
        _breaks_installment_rule(method, total)
        for method, total in zip(orders["payment_method"], orders["total_brl"], strict=True)
    ]

    valid_tracking = orders["tracking_code"].str.fullmatch(_TRACKING_CODE)
    orders["tracking_code"] = orders["tracking_code"].where(valid_tracking, None)
    for column in ("estimated_delivery", "notes"):
        orders[column] = orders[column].replace("", None)
    return orders


def _clean_categories(categories: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Type categories and count their products (categories 6-8 have none)."""
    categories["category_id"] = categories["category_id"].astype(int)
    per_category = products["category_id"].value_counts()
    categories["product_count"] = categories["category_id"].map(per_category).fillna(0).astype(int)
    return categories


def load_clean_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Read every store CSV from ``data_dir`` and return the cleaned tables.

    Args:
        data_dir: Folder holding the original CSV files.

    Returns:
        Mapping of table name to cleaned DataFrame.

    Raises:
        DataValidationError: If a foreign key dangles or an order has an unknown status or
            payment method.
    """
    raw = {name: _read_csv(data_dir, name) for name in _TABLES}
    _check_references(raw)

    promotions = _clean_promotions(raw["promotions"])
    products = _clean_products(raw["products"], promotions)
    items = _clean_order_items(raw["order_items"])
    return {
        "categories": _clean_categories(raw["categories"], products),
        "customers": _clean_customers(raw["customers"]),
        "orders": _clean_orders(raw["orders"], items, products),
        "order_items": items,
        "products": products,
        "promotions": promotions,
    }


def build_database(data_dir: Path, db_path: Path) -> None:
    """Clean the CSVs in ``data_dir`` and write them as tables of a SQLite file.

    The database is a build artifact: existing tables are replaced, so it is safe to run again.

    Args:
        data_dir: Folder holding the original CSV files.
        db_path: SQLite file to create (its parent folder is created if missing).
    """
    tables = load_clean_tables(data_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        for name, table in tables.items():
            table.to_sql(name, connection, if_exists="replace", index=False)
