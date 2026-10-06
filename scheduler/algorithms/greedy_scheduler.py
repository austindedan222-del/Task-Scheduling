"""
Hybrid greedy scheduling algorithm.

Methodology (fixed — do not change without updating the project report):

    P = user-assigned priority, raw value 1-10 (10 = highest). Never
        normalized to a 0-1 range.
    D = number of whole calendar days remaining before the deadline,
        measured as (deadline - today). Clamped at 0 so an overdue or
        due-today task never produces a negative D — it is instead
        treated as maximum urgency.
    U = urgency score = 1 / (D + 1)
    S = scheduling score = 0.7 * P + 0.3 * U

Tasks are sorted in descending order of S. Ties are broken by:
    1. Earlier deadline
    2. Shorter duration

Complexity: computing D/U/S for every task is O(n); sorting is
O(n log n); so the overall algorithm is O(n log n).
"""

from dataclasses import dataclass
from datetime import date


@dataclass
class ScheduledTask:
    """A task annotated with its computed scheduling values."""

    task: object
    days_remaining: int
    urgency: float
    score: float


def _days_remaining(deadline: date, reference_date: date) -> int:
    """Whole days between reference_date and deadline, never negative."""
    delta = (deadline - reference_date).days
    return max(delta, 0)


def compute_score(priority: int, days_remaining: int) -> tuple:
    """Return (urgency, score) for one task given its priority and D.

    Kept as a standalone function so the formula has a single source of
    truth that both the schedule view and the PDF export call into —
    neither recomputes it independently.
    """
    urgency = 1 / (days_remaining + 1)
    score = (0.7 * priority) + (0.3 * urgency)
    return urgency, score


def generate_schedule(tasks, reference_date: date = None):
    """Compute the greedy schedule for an iterable of Task objects.

    Returns a list of ScheduledTask, sorted by descending score with the
    deadline/duration tie-breakers applied.
    """
    if reference_date is None:
        reference_date = date.today()

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

    # Descending score; earlier deadline first; shorter duration first.
    scheduled.sort(
        key=lambda item: (-item.score, item.task.deadline, item.task.duration)
    )

    return scheduled
