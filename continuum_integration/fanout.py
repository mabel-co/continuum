"""Continuum integration — fan-out dispatcher.

Dispatches a single notification event to a set of consumer callables.  Each
consumer is processed independently; failures in one do not affect the others.
Results are collected into a :class:`FanoutReport`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .models import NotificationEvent


@dataclass(frozen=True)
class FanoutReport:
    """Summary of a fan-out operation.

    Attributes
    ----------
    delivered:
        Names of consumers that processed successfully.
    failed:
        Pairs of ``(name, reason)`` for consumers that raised.
    """

    delivered: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


ConsumerFn = Callable[[dict], None]


def dispatch(
    event: dict | NotificationEvent,
    consumers: list[tuple[str, ConsumerFn]],
) -> FanoutReport:
    """Run *event* through every consumer in *consumers*.

    Parameters
    ----------
    event:
        The notification to dispatch.  A :class:`NotificationEvent` or a plain
        dict.
    consumers:
        List of ``(name, fn)`` pairs where *fn* is a callable that accepts
        the event dict.

    Returns
    -------
    A :class:`FanoutReport` with per-consumer results.  A single consumer
    failure never blocks the others.
    """
    if isinstance(event, NotificationEvent):
        event_dict: dict = event.to_dict()
    else:
        event_dict = dict(event)

    report = FanoutReport()

    for name, fn in consumers:
        try:
            fn(event_dict)
            report.delivered.append(name)
        except Exception as exc:
            report.failed.append((name, str(exc)))

    return report
