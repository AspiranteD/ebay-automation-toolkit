"""
eBay category mapping: parent categories to leaf 'Other' categories.

eBay.es requires leaf-level category IDs for listings. When the source
category (from Amazon) maps to a parent-level eBay category, this module
provides the corresponding leaf 'Other' fallback.

Production uses a database table (ebay_category_map) for fine-grained
mapping. This module provides the static parent-to-leaf fallback.
"""

DEFAULT_LEAF_CATEGORY = "175837"  # Other Consumer Electronics

PARENT_TO_LEAF = {
    "293": "175837",     # Consumer Electronics -> Other Consumer Electronics
    "58058": "162",      # Computers/Tablets -> Other Computers & Networking
    "11700": "181076",   # Home & Garden -> Other Home & Garden
    "20625": "181076",   # Kitchen, Dining & Bar -> Other Home & Garden
    "631": "181076",     # Tools & Workshop -> Other Home & Garden
    "159912": "181076",  # Yard, Garden & Outdoor -> Other Home & Garden
    "888": "310",        # Sporting Goods -> Other Sporting Goods
    "6000": "175837",    # Automotive (invalid on eBay.es consumer) -> fallback
    "220": "175837",     # Toys & Hobbies -> fallback
    "26395": "175837",   # Health & Beauty -> fallback
    "11450": "175837",   # Clothing -> fallback
    "2984": "175837",    # Baby -> fallback
    "1281": "175837",    # Pet Supplies -> fallback
    "619": "175837",     # Musical Instruments -> fallback
    "1249": "175837",    # Video Games -> fallback
    "267": "175837",     # Books -> fallback
    "625": "175837",     # Cameras & Photo -> fallback
}


def ensure_leaf_category(category_id: str) -> str:
    """Convert parent categories to their leaf 'Other' equivalents."""
    cat = str(category_id).strip()
    return PARENT_TO_LEAF.get(cat, cat)


def map_source_category(
    department: str,
    category: str,
    subcategory: str,
    category_map: dict[tuple[str, str], str],
) -> str:
    """
    Map Amazon taxonomy to eBay category ID.

    Tries department, category, subcategory in order. Falls back to
    DEFAULT_LEAF_CATEGORY if no match found.
    """
    for value, source_type in [
        (department, "amazon_department"),
        (category, "amazon_category"),
        (subcategory, "amazon_subcategory"),
    ]:
        if value:
            key = (str(value).strip().lower(), source_type)
            if key in category_map:
                return ensure_leaf_category(category_map[key])
    return DEFAULT_LEAF_CATEGORY
