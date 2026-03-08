"""Internal state-machine controllers for TadoXProxyClimate.

These classes hold their own state and can be tested independently of Home
Assistant.  They only import HA helpers lazily (inside methods that schedule
timers), so a plain ``import`` in tests works without an HA bootstrap.

Architecture
------------
- ``WindowAutomationController``  – window-open/close delays & state
- ``PresenceAutomationController`` – presence-away delay & state
- ``FollowPhysicalController``    – pure-logic helper (no state, static method)
- ``SavedState``                  – lightweight snapshot dataclass
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

# Type alias for the cancel-callback returned by async_call_later
_CancelFn = Callable[[], None]
# Signature of async_call_later (or test stub)
_CallLaterFn = Callable[[Any, float, Callable], _CancelFn]


@dataclass
class SavedState:
    """Snapshot of preset and temperature for later restoration."""

    preset: str | None = None
    temp: float | None = None


# ---------------------------------------------------------------------------
# Window automation
# ---------------------------------------------------------------------------

class WindowAutomationController:
    """Manages window-sensor automation with configurable open/close delays.

    State transitions::

        idle ──(open)──► open_pending ──(delay)──► active
                ▲                                      │
                │        (close)                       │
                └───────── close_pending ◄─────────────┘
                                │ (delay expires)
                                ▼
                              idle
    """

    def __init__(self) -> None:
        self.is_active: bool = False
        self._open_timer: _CancelFn | None = None
        self._close_timer: _CancelFn | None = None
        self._saved: SavedState = SavedState()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def close_delay_active(self) -> bool:
        """True while the post-close restoration timer is running."""
        return self._close_timer is not None

    def get_saved(self) -> SavedState:
        """Return a copy of the currently saved preset/temperature."""
        return SavedState(preset=self._saved.preset, temp=self._saved.temp)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def handle_window_opened(
        self,
        hass: Any,
        delay_s: float,
        on_open_action: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> None:
        """React to a window-opened event.

        Special case: if a close-delay timer is running (window reopened
        during the restore countdown), cancel that timer and stay in frost
        protection without restarting the open-delay countdown.
        """
        if self._close_timer is not None:
            self._close_timer()
            self._close_timer = None
            _LOGGER.debug("Window reopened during close delay – staying in frost protection")
            return

        # Cancel any previously pending open timer before scheduling a new one
        if self._open_timer is not None:
            self._open_timer()
        _cl = call_later or _get_call_later()
        self._open_timer = _cl(hass, delay_s, on_open_action)
        _LOGGER.debug("Window opened – frost-protection action in %ds", delay_s)

    def handle_window_closed(
        self,
        hass: Any,
        close_delay_s: float,
        on_timer_expire: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> bool:
        """React to a window-closed event.

        Returns ``True`` when the caller should trigger an *immediate* restore
        (``close_delay_s == 0`` and the window-open mode was active).
        Returns ``False`` in all other cases (timer scheduled, nothing to do).
        """
        if self._open_timer is not None:
            self._open_timer()
            self._open_timer = None
        if self._close_timer is not None:
            self._close_timer()
            self._close_timer = None

        if not self.is_active:
            # Window closed before the open-delay fired – nothing to restore.
            return False

        if close_delay_s > 0:
            _cl = call_later or _get_call_later()
            self._close_timer = _cl(hass, close_delay_s, on_timer_expire)
            _LOGGER.debug("Window closed – restoring previous preset in %ds", close_delay_s)
            return False

        # Zero delay: caller should restore immediately (synchronously)
        return True

    # ------------------------------------------------------------------
    # State mutations called by the climate entity
    # ------------------------------------------------------------------

    def activate(self, preset: str, temp: float | None) -> None:
        """Record the pre-open state and mark window automation as active."""
        self._open_timer = None
        self._saved = SavedState(preset=preset, temp=temp)
        self.is_active = True

    def restore(self) -> SavedState:
        """Clear active state and return the saved preset/temp for restoration."""
        saved = SavedState(preset=self._saved.preset, temp=self._saved.temp)
        self._close_timer = None
        self._saved = SavedState()
        self.is_active = False
        return saved

    def update_saved(self, preset: str, temp: float | None) -> None:
        """Update the saved preset/temp without changing active state.

        Used when the user changes preset while frost protection is active
        so that the new preset is restored when the window closes.
        """
        self._saved = SavedState(preset=preset, temp=temp)

    def cancel_all(self) -> None:
        """Cancel all timers and reset to idle state (e.g. user override)."""
        if self._open_timer:
            self._open_timer()
            self._open_timer = None
        if self._close_timer:
            self._close_timer()
            self._close_timer = None
        self.is_active = False
        self._saved = SavedState()


# ---------------------------------------------------------------------------
# Presence automation
# ---------------------------------------------------------------------------

class PresenceAutomationController:
    """Manages presence-sensor automation with a configurable away delay.

    State transitions::

        home ──(away)──► away_pending ──(delay)──► active
          ▲                                            │
          └─────────────── (home) ────────────────────┘
    """

    def __init__(self) -> None:
        self.is_active: bool = False
        self._away_timer: _CancelFn | None = None
        self._saved: SavedState = SavedState()

    def handle_presence_away(
        self,
        hass: Any,
        delay_s: float,
        on_away_action: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> None:
        """React to a presence-away event; schedule the away action."""
        if self._away_timer is not None:
            self._away_timer()
        _cl = call_later or _get_call_later()
        self._away_timer = _cl(hass, delay_s, on_away_action)
        _LOGGER.debug("Presence away – switching to AWAY preset in %ds", delay_s)

    def handle_presence_home(self) -> bool:
        """React to a presence-home event; cancel any pending away timer.

        Returns ``True`` when the caller should trigger an immediate restore
        (presence-away mode was active).
        """
        if self._away_timer is not None:
            self._away_timer()
            self._away_timer = None
        return self.is_active

    def activate(self, preset: str, temp: float | None) -> None:
        """Record the pre-away state and mark presence automation as active."""
        self._away_timer = None
        self._saved = SavedState(preset=preset, temp=temp)
        self.is_active = True

    def restore(self) -> SavedState:
        """Clear active state and return the saved preset/temp for restoration."""
        saved = SavedState(preset=self._saved.preset, temp=self._saved.temp)
        self._saved = SavedState()
        self.is_active = False
        return saved

    def cancel_timer(self) -> None:
        """Cancel the pending away timer without changing the active flag."""
        if self._away_timer is not None:
            self._away_timer()
            self._away_timer = None


# ---------------------------------------------------------------------------
# Follow-physical helper (pure logic, no state)
# ---------------------------------------------------------------------------

class FollowPhysicalController:
    """Pure-logic helper for detecting physical Tado setpoint changes.

    Contains no mutable state – all inputs are passed per call, making it
    trivially testable without any mocking.
    """

    @staticmethod
    def should_follow(
        tado_setpoint: float,
        last_sent: float | None,
        last_sent_ts: float,
        threshold_c: float,
        grace_s: float,
        now: float | None = None,
    ) -> bool:
        """Return ``True`` if the Tado change looks like physical user input.

        Returns ``False`` when:

        - ``last_sent`` is ``None`` (no baseline yet).
        - The new setpoint is within ``threshold_c`` of our last command.
        - We are within ``grace_s`` of our last command (Tado still
          acknowledging via Thread/cloud).
        """
        if last_sent is None:
            return False
        if abs(tado_setpoint - last_sent) <= threshold_c:
            return False
        _now = now if now is not None else time.time()
        if _now - last_sent_ts < grace_s:
            return False
        return True


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_call_later() -> _CallLaterFn:
    """Return HA's ``async_call_later`` (imported lazily to stay HA-free at module level)."""
    from homeassistant.helpers.event import async_call_later  # noqa: PLC0415
    return async_call_later
