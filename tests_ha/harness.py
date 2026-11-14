"""The Home Assistant test harness, from whichever package provides it.

From Home Assistant 2026.11 the harness ships with Home Assistant itself as
``pytest-homeassistant``.  Older versions get it from
``pytest-homeassistant-custom-component``, whose last release is for
Home Assistant 2026.10.  Both offer the same helpers under the same names,
so the tests import them from here and run on either.
"""
try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_capture_events,
        async_fire_time_changed,
        mock_restore_cache,
        mock_restore_cache_with_extra_data,
    )
except ImportError:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_capture_events,
        async_fire_time_changed,
        mock_restore_cache,
        mock_restore_cache_with_extra_data,
    )

__all__ = [
    "MockConfigEntry",
    "async_capture_events",
    "async_fire_time_changed",
    "mock_restore_cache",
    "mock_restore_cache_with_extra_data",
]
