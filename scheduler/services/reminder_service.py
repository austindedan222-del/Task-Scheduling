"""
Deadline reminders: in-application tracking, email dispatch, and a
day-overload check.

Fulfils FR7 ("track impending deadlines in the background to send email
reminders") and the Reliability non-functional requirement ("warn the
user if they accidentally schedule more work than can physically fit in
a day") from Chapter Three. Email is sent using Django's own built-in
mail framework (django.core.mail) — no third-party notification service
or paid API — so EMAIL_BACKEND alone decides whether a message actually
leaves the machine (see task_scheduler/settings.py). None of this
touches the scheduling logic in algorithms/greedy_scheduler.py; it only
reads the same Task data and, for the overload check, each task's own
duration.
"""

from collections import defaultdict
from datetime import date

from django.core.mail import send_mail
from django.conf import settings

DUE_SOON_WINDOW_DAYS = 2

# "More work than can physically fit in a day" (Chapter 3, Reliability
# NFR) is taken literally as a 24-hour ceiling, not an 8-hour workday —
# the chapter does not specify working hours, so this is the one bound
# that needs no further assumption.
DAY_CAPACITY_MINUTES = 24 * 60


def get_due_soon_tasks(tasks, reference_date: date = None):
    """Tasks due within DUE_SOON_WINDOW_DAYS days (inclusive), not yet overdue."""
    if reference_date is None:
        reference_date = date.today()

    due_soon = [
        task for task in tasks
        if 0 <= (task.deadline - reference_date).days <= DUE_SOON_WINDOW_DAYS
    ]
    return sorted(due_soon, key=lambda task: task.deadline)


def get_overdue_tasks(tasks, reference_date: date = None):
    """Tasks whose deadline has already passed."""
    if reference_date is None:
        reference_date = date.today()

    overdue = [task for task in tasks if task.deadline < reference_date]
    return sorted(overdue, key=lambda task: task.deadline)


def get_overloaded_days(tasks):
    """Deadline dates where the combined duration of tasks due that day
    exceeds DAY_CAPACITY_MINUTES.

    Returns a list of dicts — {"date": ..., "total_minutes": ...} — sorted
    by date, one entry per overloaded day. An empty list means no day is
    overloaded.
    """
    totals = defaultdict(int)
    for task in tasks:
        totals[task.deadline] += task.duration

    overloaded = [
        {"date": day, "total_minutes": minutes}
        for day, minutes in totals.items()
        if minutes > DAY_CAPACITY_MINUTES
    ]
    return sorted(overloaded, key=lambda item: item["date"])


def send_reminder_email(user, due_soon, overdue):
    """Email one user a plain-text summary of their due-soon/overdue tasks.

    Returns True if a message was sent, False if there was nothing to
    send or the user has no email address on file (never raises for
    either of those ordinary cases).
    """
    if not user.email:
        return False
    if not due_soon and not overdue:
        return False

    lines = [f"Hello {user.username},", ""]
    if overdue:
        lines.append("Overdue tasks:")
        lines += [f"  - {t.title} (was due {t.deadline})" for t in overdue]
        lines.append("")
    if due_soon:
        lines.append("Due soon:")
        lines += [f"  - {t.title} (due {t.deadline})" for t in due_soon]
        lines.append("")
    lines.append("— Task Scheduler")

    send_mail(
        subject="Task Scheduler: upcoming deadlines",
        message="\n".join(lines),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[user.email],
        fail_silently=False,
    )
    return True
