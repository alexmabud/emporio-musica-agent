"""Read-only product queries over the cleaned SQLite database.

Every query is parameterized and returns compact, customer-ready dictionaries: money is always text
such as ``R$ 2.199,00`` so the grounding check can compare it with what the agent says.
"""

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from emporio_agent.data_loader import fold_text
from emporio_agent.db import query
from emporio_agent.pricing import format_brl, payment_options, pix_price, price_product

MAX_SEARCH_RESULTS = 8
MAX_ALTERNATIVES = 3
MAX_CANDIDATES = 5

# Minimum fuzzy-match score (0-100) for a typed name to be accepted as a product.
_MATCH_THRESHOLD = 85
# Names typed without spaces ("ck20") are compared squashed, which needs a longer, stricter match.
_MIN_COMPACT_LENGTH = 4
_COMPACT_THRESHOLD = 95

_AVAILABILITY = {
    "discontinued": "Descontinuado (não faz mais parte do catálogo)",
    "coming_soon": "Em breve (ainda não disponível para compra)",
}


def _match_key(text: str) -> str:
    """Lowercase, strip accents and turn punctuation into spaces: 'GF-3D' becomes 'gf 3d'."""
    return re.sub(r"[^a-z0-9]+", " ", fold_text(text)).strip()


def _format_percent(percent: Decimal) -> str:
    """Write a promotion percent without trailing zeros, e.g. ``20%`` or ``7.5%``."""
    return f"{percent.normalize():f}%"


class Catalog:
    """Product lookups for the agent tools.

    Args:
        db_path: SQLite file built by ``data_loader.build_database``.
    """

    def __init__(self, db_path: Path) -> None:
        """Remember where the database lives; connections are opened per query."""
        self._db_path = db_path

    def _summary(self, row: sqlite3.Row) -> dict[str, Any]:
        """Describe a product in a few customer-ready fields."""
        quote = price_product(row["price_brl"], row["active_promo_percent"])
        percent = quote.promo_percent
        return {
            "name": row["name"],
            "category": row["category_name"],
            "list_price": format_brl(quote.list_price),
            "discount": None if percent is None else _format_percent(percent),
            "final_price": format_brl(quote.final_price),
            "in_stock": True,
        }

    def search(
        self,
        *,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        string_type: str | None = None,
        has_electronics: bool | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """Search sellable products, cheapest first.

        Args:
            category: Exact category name, or ``None`` for any.
            min_price: Lowest *final* price (promotion included), or ``None``.
            max_price: Highest *final* price (promotion included), or ``None``.
            string_type: ``nylon`` or ``steel``, or ``None`` for any.
            has_electronics: Whether the instrument has a pickup/preamp, or ``None`` for any.
            keyword: Words that must all appear in the name (accents and case are ignored).

        Returns:
            ``total_matches``, ``shown`` (capped) and the ``products`` list.
        """
        rows = query(
            self._db_path,
            "SELECT p.*, c.name AS category_name FROM products p "
            "JOIN categories c ON c.category_id = p.category_id "
            "WHERE p.is_sellable = 1 AND (? IS NULL OR c.name = ?) "
            "AND (? IS NULL OR p.string_type = ?) AND (? IS NULL OR p.has_electronics = ?)",
            (category, category, string_type, string_type, has_electronics, has_electronics),
        )
        words = _match_key(keyword or "").split()
        matches = []
        for row in rows:
            quote = price_product(row["price_brl"], row["active_promo_percent"])
            if min_price is not None and quote.final_price < Decimal(str(min_price)):
                continue
            if max_price is not None and quote.final_price > Decimal(str(max_price)):
                continue
            key = _match_key(row["name_search"])
            if not all(word in key for word in words):
                continue
            matches.append((quote.final_price, row))
        matches.sort(key=lambda match: (match[0], match[1]["name"]))
        shown = matches[:MAX_SEARCH_RESULTS]
        result: dict[str, Any] = {
            "total_matches": len(matches),
            "shown": len(shown),
            "products": [self._summary(row) for _, row in shown],
        }
        if not matches:
            result["note"] = self._empty_note(category)
        return result

    def _empty_note(self, category: str | None) -> str:
        """Explain an empty result, telling an empty category apart from a too-narrow filter."""
        if category is not None:
            counts = query(
                self._db_path, "SELECT product_count FROM categories WHERE name = ?", (category,)
            )
            if counts and counts[0]["product_count"] == 0:
                return f"The category '{category}' exists but the store has no products in it."
        return "No available product matches these filters."

    def _find_rows(self, name: str) -> list[sqlite3.Row]:
        """Find the products whose names best match ``name``.

        Accents, case, punctuation and word order are ignored, and a name typed without the space
        ("ck20") still matches "CK-20".

        Returns:
            The rows sharing the top score: one row for a clear match, several when the name is
            ambiguous (e.g. just a brand), none when nothing is close enough.
        """
        rows = query(
            self._db_path,
            "SELECT p.*, c.name AS category_name FROM products p "
            "JOIN categories c ON c.category_id = p.category_id",
        )
        typed = _match_key(name)
        keys = [_match_key(row["name_search"]) for row in rows]
        top = self._top_matches(typed, keys, fuzz.token_set_ratio, _MATCH_THRESHOLD)
        compact = typed.replace(" ", "")
        if not top and len(compact) >= _MIN_COMPACT_LENGTH:
            squashed = [key.replace(" ", "") for key in keys]
            top = self._top_matches(compact, squashed, fuzz.partial_ratio, _COMPACT_THRESHOLD)
        return [rows[index] for index in top]

    @staticmethod
    def _top_matches(query: str, choices: list[str], scorer: Any, threshold: int) -> list[int]:
        """Indexes of the choices that share the best score, if it reaches ``threshold``."""
        scored = process.extract(query, choices, scorer=scorer, limit=None, score_cutoff=threshold)
        if not scored:
            return []
        best = max(score for _, score, _ in scored)
        return [index for _, score, index in scored if score == best]

    def _resolve(self, name: str) -> tuple[sqlite3.Row | None, dict[str, Any] | None]:
        """Pick the one product a typed name refers to, or explain why that is not possible.

        Returns:
            ``(row, None)`` for a clear match, otherwise ``(None, answer)`` where ``answer`` says
            the product was not found or the name is ambiguous.
        """
        matches = self._find_rows(name)
        if not matches:
            return None, {
                "found": False,
                "note": "No product with that name. Use search_products to browse the catalog.",
            }
        if len(matches) > 1:
            return None, {
                "found": False,
                "ambiguous": True,
                "candidates": sorted(match["name"] for match in matches)[:MAX_CANDIDATES],
                "note": "Several products fit this name; ask the customer which one they mean.",
            }
        return matches[0], None

    def details(self, name: str) -> dict[str, Any]:
        """Describe one product, whether or not it can be bought today.

        Args:
            name: Product name as typed by the customer (approximate).

        Returns:
            Prices, PIX price, availability, specs and (when reliable) the description.
        """
        row, problem = self._resolve(name)
        if row is None:
            return problem or {"found": False}
        promo = row["active_promo_percent"]
        unit_pix = pix_price(row["price_brl"], promo)
        if row["is_sellable"]:
            availability = "Disponível"
        elif row["status"] == "active":
            availability = "Esgotado (sem estoque no momento)"
        else:
            availability = _AVAILABILITY[row["status"]]
        result = {
            "found": True,
            **self._summary(row),
            "pix_price": None if unit_pix is None else format_brl(unit_pix),
            "pix_note": (
                "PIX discount does not apply: the product is already on promotion."
                if unit_pix is None
                else "PIX pays 5% less than the list price."
            ),
            "availability": availability,
            "specs": json.loads(row["specs"]),
            "description": row["description"],
        }
        if not row["is_sellable"]:
            result["in_stock"] = False
            result["alternatives"] = self._alternatives(row)
        return result

    def _alternatives(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        """Suggest sellable products of the same category, similar and close in price.

        Similarity is the same string material, then the same electronics, then the nearest price.
        """
        target = price_product(row["price_brl"], row["active_promo_percent"]).final_price
        candidates = query(
            self._db_path,
            "SELECT p.*, c.name AS category_name FROM products p "
            "JOIN categories c ON c.category_id = p.category_id "
            "WHERE p.is_sellable = 1 AND p.category_id = ? AND p.product_id != ?",
            (row["category_id"], row["product_id"]),
        )

        def rank(candidate: sqlite3.Row) -> tuple[bool, bool, Decimal, str]:
            final = price_product(candidate["price_brl"], candidate["active_promo_percent"])
            return (
                candidate["string_type"] != row["string_type"],
                candidate["has_electronics"] != row["has_electronics"],
                abs(final.final_price - target),
                candidate["name"],
            )

        best = sorted(candidates, key=rank)[:MAX_ALTERNATIVES]
        return [self._summary(candidate) for candidate in best]

    def active_promotions(self) -> dict[str, Any]:
        """List the promotions running now on products that can be bought, biggest discount first.

        Returns:
            ``total``, the ``promotions`` (original price, percent, final price) and a ``note``.
        """
        rows = query(
            self._db_path,
            "SELECT p.*, c.name AS category_name FROM products p "
            "JOIN categories c ON c.category_id = p.category_id "
            "WHERE p.is_sellable = 1 AND p.active_promo_percent IS NOT NULL "
            "ORDER BY p.active_promo_percent DESC, p.name",
        )
        return {
            "total": len(rows),
            "promotions": [self._summary(row) for row in rows],
            "note": (
                "PIX discount does not apply on promotional prices. "
                "Promotions are subject to stock availability."
            ),
        }

    def payment(self, name: str, quantity: int = 1) -> dict[str, Any]:
        """Price and payment plans for ``quantity`` units of one product.

        Every installment plan is spelled out, so each value the agent may quote is in the result.

        Args:
            name: Product name as typed by the customer (approximate).
            quantity: Number of units, at least 1.

        Returns:
            Unit and total prices, PIX, installment plans and combined-payment permission; or, for a
            product that cannot be bought, its availability and alternatives.
        """
        row, problem = self._resolve(name)
        if row is None:
            return problem or {"found": False}
        if not row["is_sellable"]:
            unavailable = self.details(name)
            return {
                "found": True,
                "available": False,
                "name": row["name"],
                "availability": unavailable["availability"],
                "alternatives": unavailable["alternatives"],
            }
        options = payment_options(row["price_brl"], row["active_promo_percent"], quantity)
        return {
            "found": True,
            "available": True,
            "name": row["name"],
            "quantity": options.quantity,
            "unit_price": format_brl(options.price.final_price),
            "total": format_brl(options.total),
            "discount": None
            if options.price.promo_percent is None
            else _format_percent(options.price.promo_percent),
            "pix_price": None if options.pix_price is None else format_brl(options.pix_price),
            "pix_total": None if options.pix_total is None else format_brl(options.pix_total),
            "pix_note": (
                "PIX discount does not apply: the product is already on promotion."
                if options.pix_total is None
                else "PIX pays 5% less than the list price."
            ),
            "max_installments": options.installments[-1].count,
            "installment_plans": [plan.describe() for plan in options.installments],
            "combined_payment_allowed": options.combined_payment_allowed,
        }
