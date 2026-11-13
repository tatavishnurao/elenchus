"""UI variants for the mock ERP.

The baseline is the form the agent was written against. Every other variant
changes one thing about the page structure. The server accepts each variant's
field names, so a drifted page still works for a human. Only an agent that
assumes the baseline layout goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BASELINE = "baseline"

HEADER_KEYS = ("customer", "source_ref", "notes")
LINE_KEYS = ("sku", "color", "sizes", "quantity", "unit_price")

DEFAULT_LABELS = {
    "customer": "Customer",
    "source_ref": "Web order ref",
    "notes": "Notes",
    "sku": "Product",
    "color": "Color",
    "size": "Size",
    "quantity": "Quantity",
    "unit_price": "Unit price",
    "S": "S",
    "M": "M",
    "L": "L",
    "XL": "XL",
    "2XL": "2XL",
}

DEFAULT_NAMES = {
    "customer": "customer_id",
    "source_ref": "source_ref",
    "notes": "notes",
    "sku": "sku",
    "color": "color",
    "size": "size",
    "quantity": "quantity",
    "unit_price": "unit_price",
    "S": "qty_s",
    "M": "qty_m",
    "L": "qty_l",
    "XL": "qty_xl",
    "2XL": "qty_2xl",
}


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    labels: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    header_order: tuple[str, ...] = HEADER_KEYS
    line_order: tuple[str, ...] = LINE_KEYS
    # Baseline puts the primary button first and Reset second.
    buttons_swapped: bool = False
    # "grid": one input per size. "dropdown": one size select plus a quantity.
    size_widget: str = "grid"
    finish_label: str = "Finish order"

    def label(self, key: str) -> str:
        return self.labels.get(key, DEFAULT_LABELS[key])

    def field_name(self, key: str) -> str:
        return self.names.get(key, DEFAULT_NAMES[key])


VARIANTS: dict[str, Variant] = {
    v.name: v
    for v in (
        Variant(BASELINE, "Reference layout."),
        Variant(
            "renamed",
            "Labels and some input names renamed.",
            labels={
                "customer": "Account",
                "source_ref": "Reference",
                "sku": "Item",
                "quantity": "Total qty",
                "unit_price": "Price each",
            },
            names={
                "customer": "account_id",
                "quantity": "total_qty",
                "unit_price": "price_each",
            },
            finish_label="Submit order",
        ),
        Variant(
            "reordered",
            "Same fields in a different order.",
            header_order=("notes", "source_ref", "customer"),
            line_order=("color", "sku", "unit_price", "sizes", "quantity"),
        ),
        Variant(
            "moved_save",
            "Reset button now comes before the save button.",
            buttons_swapped=True,
        ),
        Variant(
            "size_dropdown",
            "Size grid replaced by a size dropdown and one quantity.",
            size_widget="dropdown",
            line_order=("sku", "color", "size", "quantity", "unit_price"),
        ),
    )
}


def get_variant(name: str | None) -> Variant:
    key = name or BASELINE
    if key not in VARIANTS:
        raise KeyError(key)
    return VARIANTS[key]
