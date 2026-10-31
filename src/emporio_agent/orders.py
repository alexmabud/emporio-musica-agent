"""Order lookups guarded by an identity check (LGPD).

An order is only described to the customer who owns it, proven with the e-mail or phone on their
registration. A failed check never says *why* it failed, so nobody can probe which orders exist.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

from emporio_agent.db import query
from emporio_agent.pricing import format_brl

_NOT_FOUND = {
    "verified": False,
    "message": (
        "Could not find an order with these details. Ask the customer to check the order number "
        "and the e-mail or phone used in the registration."
    ),
}


_NEEDS_PHONE = {
    "verified": False,
    "needs_phone": True,
    "message": (
        "For the customer's security, this e-mail alone cannot release the order. Ask for the "
        "phone number on the registration and call this tool again with it."
    ),
}


def _classify(contact: str) -> tuple[str, str] | None:
    """Tell an e-mail from a phone and normalize it, or return ``None`` if it is neither.

    Phones keep digits only and lose a leading country code (55); e-mails are lowercased.
    """
    text = contact.strip()
    if "@" in text:
        return "email", text.lower()
    digits = re.sub(r"\D", "", text)
    if len(digits) > 11 and digits.startswith("55"):
        digits = digits[2:]
    return ("phone", digits) if len(digits) == 11 else None


def _format_date(iso: str | None) -> str | None:
    """Turn ``2026-02-28`` into ``28/02/2026`` (``None`` stays ``None``)."""
    return None if iso is None else date.fromisoformat(iso).strftime("%d/%m/%Y")


class Orders:
    """Order status lookups for the agent tools.

    Args:
        db_path: SQLite file built by ``data_loader.build_database``.
    """

    def __init__(self, db_path: Path) -> None:
        """Remember where the database lives; connections are opened per query."""
        self._db_path = db_path

    def status(self, order_id: int, contact: str) -> dict[str, Any]:
        """Describe an order if ``contact`` proves the caller owns it.

        Args:
            order_id: Order number given by the customer.
            contact: E-mail or phone from the customer's registration.

        Returns:
            The order details when verified, otherwise a generic ``verified: False`` answer.
        """
        rows = query(
            self._db_path,
            "SELECT o.*, c.name AS customer_name, c.email_normalized, c.phone_digits, "
            "c.email_is_shared "
            "FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
            "WHERE o.order_id = ?",
            (order_id,),
        )
        identifier = _classify(contact)
        if not rows or identifier is None:
            return dict(_NOT_FOUND)
        order = rows[0]
        kind, value = identifier
        if value != (order["email_normalized"] if kind == "email" else order["phone_digits"]):
            return dict(_NOT_FOUND)
        if kind == "email" and order["email_is_shared"]:
            return dict(_NEEDS_PHONE)
        items = query(
            self._db_path,
            "SELECT p.name, i.quantity FROM order_items i "
            "JOIN products p ON p.product_id = i.product_id WHERE i.order_id = ? "
            "ORDER BY i.rowid",
            (order_id,),
        )
        result: dict[str, Any] = {
            "verified": True,
            "order_id": order["order_id"],
            "customer_first_name": order["customer_name"].split()[0],
            "order_date": _format_date(order["order_date"]),
            "status": order["status_pt"],
            "payment_method": order["payment_pt"],
            "total": format_brl(order["total_brl"]),
            "items": [{"name": item["name"], "quantity": item["quantity"]} for item in items],
            "tracking_code": order["tracking_code"],
            "estimated_delivery": _format_date(order["estimated_delivery"]),
            "notes": order["notes"],
        }
        if order["tracking_code"] is None:
            result["tracking_note"] = "No tracking code yet: it is sent when the order ships."
        return result
