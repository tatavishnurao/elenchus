"""Fixed reference data for the mock ERP.

Nothing here is random. The order generator imports these lists so source
orders only refer to customers and products that exist in the ERP.
"""

from __future__ import annotations

SIZES = ("S", "M", "L", "XL", "2XL")

COLORS = ("Black", "White", "Navy", "Heather Grey", "Forest Green", "Red")

# (name, email, city). Two names appear twice on purpose, in different cities,
# so a name alone does not identify a customer.
CUSTOMERS: tuple[tuple[str, str, str], ...] = (
    ("Northwind Outfitters", "orders@northwind-outfitters.example", "Austin"),
    ("Northwind Outfitters", "accounts@northwind-outfitters.example", "Denver"),
    ("Harbor Light Supply", "buyer@harborlight.example", "Portland"),
    ("Pinecrest Athletics", "team@pinecrest-athletics.example", "Boise"),
    ("Pinecrest Athletics", "orders@pinecrest-athletics.example", "Spokane"),
    ("Lakeside Brewing Co", "merch@lakesidebrewing.example", "Madison"),
    ("Redwood Dental Group", "office@redwooddental.example", "Sacramento"),
    ("Summit Trail Guides", "hello@summittrail.example", "Salt Lake City"),
    ("Copperline Electric", "admin@copperline.example", "Tucson"),
    ("Maple & Vine Cafe", "orders@maplevine.example", "Burlington"),
    ("Granite State Roofing", "crew@granitestateroofing.example", "Concord"),
    ("Blue Heron Press", "studio@blueheronpress.example", "Seattle"),
)

_PRODUCT_NAMES = (
    "Classic Tee",
    "Heavy Tee",
    "Long Sleeve Tee",
    "Crew Sweatshirt",
    "Zip Hoodie",
    "Pullover Hoodie",
    "Pique Polo",
    "Performance Polo",
    "Work Jacket",
    "Fleece Vest",
)

# (sku, name, list price in cents). 20 SKUs: each name in two weights.
PRODUCTS: tuple[tuple[str, str, int], ...] = tuple(
    (
        f"{'AB'[w]}{1001 + i:04d}",
        f"{name} {('Standard', 'Premium')[w]}",
        1000 + 350 * i + 600 * w,
    )
    for w in range(2)
    for i, name in enumerate(_PRODUCT_NAMES)
)
