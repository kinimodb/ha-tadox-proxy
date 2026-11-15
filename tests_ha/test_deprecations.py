"""Home Assistant must find no deprecated API in the integration."""
from __future__ import annotations

import importlib.util
import logging
import sys

from .helpers import CONTROL_INTERVAL_S, DOMAIN, async_advance, async_setup_proxy


def _create_entity_class_again() -> None:
    """Run the climate module once more, under another name.

    Home Assistant checks an entity class for deprecated members at the
    moment the class is created.  For the integration that is its first
    import in the test session, usually in an earlier test.  Running the
    module again creates the class again, now while the test reads the log.
    """
    import custom_components.tadox_proxy.climate as climate

    spec = importlib.util.spec_from_file_location(
        f"{climate.__package__}.climate_checked_again", climate.__file__
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up while the class is built.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]


async def test_home_assistant_reports_no_deprecated_api(hass, freezer, caplog):
    """Setup, one regulation cycle and the entity class log no deprecation.

    Home Assistant 2026.11 logs "provides the deprecated ..., this will stop
    working in Home Assistant 2027.11" for a climate entity that only has the
    old temperature names.  Reads of an old name inside the integration are
    guarded without Home Assistant in tests/test_climate_hardening.py.
    """
    caplog.set_level(logging.WARNING)

    proxy = await async_setup_proxy(hass)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    _create_entity_class_again()

    # Home Assistant fills these two from the names it reads on this version.
    assert proxy.state.attributes["current_temperature"] == 20.0
    assert proxy.state.attributes["temperature"] == 20.0

    reports = [
        f"{record.name}: {record.getMessage()}"
        for record in caplog.records
        if DOMAIN in f"{record.name} {record.getMessage()}"
        and any(
            word in record.getMessage().lower()
            for word in ("deprecated", "stop working")
        )
    ]
    assert reports == []
