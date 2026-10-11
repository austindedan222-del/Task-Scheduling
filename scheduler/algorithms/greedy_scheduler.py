"""
Hybrid greedy scheduling algorithm.

Methodology (fixed — do not change without updating the project report):

    P = user-assigned priority, raw value 1-10 (10 = highest). Never
        normalized to a 0-1 range.
    D = FRACTIONAL number of days remaining before the deadline,
        measured as (deadline - reference) in days now that deadlines
        carry a time of day. Clamped at 0 so an overdue or due-now task
        never produces a negative D — it is instead treated as maximum
        urgency.
    U = urgency score = 1 / (D + 1)
    S = scheduling score = 0.7 * P + 0.3 * U

Tasks are sorted in descending order of S. Ties are broken by:
    1. Earlier deadline
    2. Shorter duration (in normalized minutes, whatever unit the task
       stores its duration in)

Complexity: computing D/U/S for every task is O(n); sorting is
O(n log n); so the overall algorithm is O(n log n).
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from django.utils import timezone


@dataclass
class ScheduledTask:
    """A task annotated with its computed scheduling values."""

    task: object
    days_remaining: float
    urgency: float
    score: float


def _as_datetime(value) -> datetime:
    """Normalize a date/datetime into an aware datetime.

    Deadlines are DateTimes, but callers may still hand us a plain date
    (midnight of that day) or a naive datetime; both are made
    timezone-aware so the subtraction below is always well-defined.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.combine(value, time.min)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _days_remaining(deadline, reference) -> float:
    """Fractional days between reference and deadline, never negative."""
    delta = (_as_datetime(deadline) - _as_datetime(reference)).total_seconds()
    return max(delta / 86400.0, 0.0)


def compute_score(priority: int, days_remaining) -> tuple:
    """Return (urgency, score) for one task given its priority and D.

    """
    urgency = 1 / (days_remaining + 1)
    score = (0.7 * priority) + (0.3 * urgency)
    return urgency, score


def generate_schedule(tasks, reference_date=None):
    """Compute the greedy schedule for an iterable of Task objects.

    """
    if reference_date is None:
        reference_date = timezone.now()

    scheduled = []
    for task in tasks:
        days_remaining = _days_remaining(task.deadline, reference_date)
        urgency, score = compute_score(task.priority, days_remaining)
        scheduled.append(
            ScheduledTask(
                task=task,
                days_remaining=days_remaining,
                urgency=urgency,
                score=score,
            )
        )

    # Descending score; earlier deadline first; shorter duration first
    # (compared in normalized minutes so "1 month" sorts after "1 hour").
    scheduled.sort(
        key=lambda item: (
            -item.score,
            item.task.deadline,
            item.task.duration_minutes,
        )
    )

    return scheduled
