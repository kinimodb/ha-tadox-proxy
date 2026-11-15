"""Every translation must have exactly the texts strings.json has.

A key missing in a translation shows up in Home Assistant as a raw key
(for example an error in the options dialog without any text).
"""
import json
import os

import pytest

_COMP_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "tadox_proxy"
)
_TRANSLATIONS_DIR = os.path.join(_COMP_DIR, "translations")


def _keys(texts: dict, prefix: str = "") -> set[str]:
    """Return the dotted path of every text in a (nested) strings file."""
    keys: set[str] = set()
    for key, value in texts.items():
        if isinstance(value, dict):
            keys |= _keys(value, f"{prefix}{key}.")
        else:
            keys.add(f"{prefix}{key}")
    return keys


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("language", sorted(os.listdir(_TRANSLATIONS_DIR)))
def test_translation_has_the_keys_of_strings_json(language):
    expected = _keys(_load(os.path.join(_COMP_DIR, "strings.json")))
    actual = _keys(_load(os.path.join(_TRANSLATIONS_DIR, language)))
    assert sorted(expected - actual) == [], "texts missing in the translation"
    assert sorted(actual - expected) == [], "texts unknown to strings.json"


@pytest.mark.parametrize("language", ["cs.json", "de.json", "en.json"])
def test_options_dialog_errors_are_translated(language):
    """The errors the options dialog can report (config_flow.py)."""
    errors = _load(os.path.join(_TRANSLATIONS_DIR, language))["options"]["error"]
    assert {"gain_thresholds", "needs_heat_thresholds"} <= errors.keys()
