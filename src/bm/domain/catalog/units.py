"""Default units, shared by migration 0016 and `CatalogService.ensure_default_units`.

One definition so a business created after the migration ran gets exactly the same set
as one created before it. `allows_fraction` is the field that matters: it is what makes
E-18 enforceable.
"""

from __future__ import annotations

#: `(code, name, allows_fraction, decimal_places)`.
DEFAULT_UNITS: tuple[tuple[str, str, bool, int], ...] = (
    ("NOS", "Numbers", False, 0),
    ("PCS", "Pieces", False, 0),
    ("SET", "Set", False, 0),
    ("PAIR", "Pair", False, 0),
    ("BOX", "Box", False, 0),
    ("LTR", "Litre", True, 3),
    ("ML", "Millilitre", True, 0),
    ("KG", "Kilogram", True, 3),
    ("GM", "Gram", True, 0),
    ("MTR", "Metre", True, 2),
    ("FT", "Foot", True, 2),
)
