"""Import and construct every variant, so dataclass or lookup bugs fail fast."""

from __future__ import annotations

import dataclasses

import pytest

from elenchus.erp import variants
from elenchus.erp.variants import (
    BASELINE,
    DEFAULT_LABELS,
    DEFAULT_NAMES,
    VARIANTS,
    Variant,
    get_variant,
)

ALL_KEYS = sorted(DEFAULT_NAMES)


def test_baseline_present():
    assert BASELINE in VARIANTS
    assert get_variant(None) is VARIANTS[BASELINE]


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        get_variant("does-not-exist")


@pytest.mark.parametrize("name", list(VARIANTS))
def test_variant_constructs_and_resolves(name):
    v = VARIANTS[name]
    assert isinstance(v, Variant)
    assert v.name == name
    # Rebuild from its own fields: catches field/method name clashes.
    rebuilt = Variant(**{f.name: getattr(v, f.name) for f in dataclasses.fields(Variant)})
    assert rebuilt == v
    for key in ALL_KEYS:
        assert isinstance(v.field_name(key), str) and v.field_name(key)
        assert isinstance(v.label(key), str) and v.label(key)
    for key in (*v.header_order, *v.line_order):
        assert key == "sizes" or key in DEFAULT_NAMES
    assert v.size_widget in ("grid", "dropdown")
    if v.size_widget == "dropdown":
        assert "size" in v.line_order and "sizes" not in v.line_order
    else:
        assert "sizes" in v.line_order and "size" not in v.line_order


def test_field_names_unique_per_variant():
    for v in VARIANTS.values():
        names = [v.field_name(k) for k in DEFAULT_NAMES]
        assert len(names) == len(set(names)), v.name


def test_module_has_no_name_method_clash():
    assert callable(variants.Variant.field_name)
    assert "name" in {f.name for f in dataclasses.fields(Variant)}
