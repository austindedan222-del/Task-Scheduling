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
duration normalized to minutes.

Deadlines carry a time of day now, but reminder windows are still
calendar-day based (a task due later today is "due soon", a task whose
deadline date has passed is "overdue"), matching the behaviour the
system had when deadlines were plain dates. Completed tasks are
finished work, so they are skipped everywhere in this module: they are
never "due soon", never "overdue", and no longer consume day capacity.
"""

from collections import defaultdict
from datetime import date
import logging
import time

from smtplib import (
    SMTPException,
    SMTPRecipientsRefused,
    SMTPResponseException,
)

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from scheduler.models import Task

logger = logging.getLogger(__name__)

DUE_SOON_WINDOW_DAYS = 2

# Delivery retry policy (FR7 reliability): a transient SMTP hiccup
# (4xx reply, dropped connection, timeout) is retried with a short
# linear backoff before the reminder for that one recipient is given
# up on. Permanent failures (5xx — bad credentials, rejected
# recipient) are NOT retried; they are logged and raised immediately
# so the caller can skip to the next user.
SMTP_RETRY_ATTEMPTS = 3
SMTP_RETRY_DELAYS = (1, 3)  # seconds to wait after attempt 1 and attempt 2


class EmailSendError(Exception):
    """Delivery failed after all retries (or failed permanently).

    Carries which recipient failed and why. Callers decide what to do:
    the send_reminders command logs it and moves to the next user; the
    dashboard button shows an error message instead of crashing.
    """


def _is_transient_smtp_error(exc) -> bool:
    """True if retrying could plausibly succeed.

    - 4xx SMTP replies (e.g. 451 temporary local problem) → transient.
    - 5xx SMTP replies (535 bad credentials, 550 rejected recipient)
      → permanent, retrying only wastes time.
    - Connection-level problems (refused, reset, DNS, timeout,
      SMTPServerDisconnected) → transient.
    """
    if isinstance(exc, SMTPRecipientsRefused):
        # {address: (code, message)} — transient only if some server
        # answered with a 4xx "try again later".
        codes = [code for code, _ in exc.recipients.values()]
        return any(400 <= code < 500 for code in codes)
    if isinstance(exc, SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    # Everything else: plain SMTPException (disconnects, protocol
    # hiccups) or OSError (socket/DNS/timeout). Retry those.
    return isinstance(exc, (SMTPException, OSError))

# "More work than can physically fit in a day" (Chapter 3, Reliability
# NFR) is taken literally as a 24-hour ceiling, not an 8-hour workday —
# the chapter does not specify working hours, so this is the one bound
# that needs no further assumption.
DAY_CAPACITY_MINUTES = 24 * 60


def _deadline_date(task) -> date:
    """The LOCAL calendar date of a task's deadline.

    Deadline instants come back from the database in UTC, so reading
    .date() off them directly would put a midnight deadline in the
    previous day's date for any user east of UTC (this project's
    TIME_ZONE is Africa/Lagos). Convert to the active time zone first —
    reminder windows are calendar-day based from the user's point of
    view.
    """
    deadline = task.deadline
    if timezone.is_aware(deadline):
        deadline = timezone.localtime(deadline)
    return deadline.date()


def _is_open(task) -> bool:
    """True while the task still counts as outstanding work."""
    return task.status != Task.STATUS_COMPLETED


def get_due_soon_tasks(tasks, reference_date: date = None):
    """Open tasks due within DUE_SOON_WINDOW_DAYS days (inclusive), not yet overdue."""
    if reference_date is None:
        reference_date = date.today()

    due_soon = [
        task for task in tasks
        if _is_open(task)
        and 0 <= (_deadline_date(task) - reference_date).days <= DUE_SOON_WINDOW_DAYS
    ]
    return sorted(due_soon, key=lambda task: task.deadline)


def get_overdue_tasks(tasks, reference_date: date = None):
    """Open tasks whose deadline date has already passed."""
    if reference_date is None:
        reference_date = date.today()

    overdue = [
        task for task in tasks
        if _is_open(task) and _deadline_date(task) < reference_date
    ]
    return sorted(overdue, key=lambda task: task.deadline)


def get_overloaded_days(tasks):
    """Deadline dates where the combined duration of tasks due that day
    exceeds DAY_CAPACITY_MINUTES.

    Durations are normalized to minutes through Task.duration_minutes,
    so "2 hours + 90 minutes" on the same day is compared against the
    24-hour capacity correctly. Completed tasks are ignored.

    Returns a list of dicts — {"date": ..., "total_minutes": ...} — sorted
    by date, one entry per overloaded day. An empty list means no day is
    overloaded.
    """
    totals = defaultdict(int)
    for task in tasks:
        if not _is_open(task):
            continue
        totals[_deadline_date(task)] += task.duration_minutes

    overloaded = [
        {"date": day, "total_minutes": minutes}
        for day, minutes in totals.items()
        if minutes > DAY_CAPACITY_MINUTES
    ]
    return sorted(overloaded, key=lambda item: item["date"])


def _format_deadline(deadline) -> str:
    """Deadline as local 'YYYY-MM-DD HH:MM' for a plain-text email."""
    if timezone.is_aware(deadline):
        deadline = timezone.localtime(deadline)
    return deadline.strftime('%Y-%m-%d %H:%M')


def send_reminder_email(user, due_soon, overdue):
    """Email one user a plain-text summary of their due-soon/overdue tasks.

    Returns True if a message was sent, False if there was nothing to
    send or the user has no email address on file (never raises for
    either of those ordinary cases).

    Delivery failures are retried with backoff for transient SMTP
    errors (see SMTP_RETRY_ATTEMPTS / _is_transient_smtp_error). If
    delivery still fails, logs which user and which error, then raises
    EmailSendError so the caller can isolate the failure — one bad
    recipient must not abort the rest of a reminder run.
    """
    if not user.email:
        return False
    if not due_soon and not overdue:
        return False

    lines = [f"Hello {user.username},", ""]
    if overdue:
        lines.append("Overdue tasks:")
        lines += [
            f"  - {t.title} (was due {_format_deadline(t.deadline)})" for t in overdue
        ]
        lines.append("")
    if due_soon:
        lines.append("Due soon:")
        lines += [
            f"  - {t.title} (due {_format_deadline(t.deadline)})" for t in due_soon
        ]
        lines.append("")
    lines.append("— Task Scheduler")

    last_error = None
    for attempt in range(1, SMTP_RETRY_ATTEMPTS + 1):
        try:
            send_mail(
                subject="Task Scheduler: upcoming deadlines",
                message="\n".join(lines),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[user.email],
                fail_silently=False,
            )
            return True
        except (SMTPException, OSError) as exc:
            last_error = exc
            permanent = not _is_transient_smtp_error(exc)
            if permanent or attempt == SMTP_RETRY_ATTEMPTS:
                break
            delay = SMTP_RETRY_DELAYS[min(attempt - 1, len(SMTP_RETRY_DELAYS) - 1)]
            logger.warning(
                "Reminder email to %s <%s> attempt %d/%d failed (%s); "
                "retrying in %ds",
                user.username, user.email, attempt, SMTP_RETRY_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)

    error = EmailSendError(
        f"Reminder email to {user.username} <{user.email}> failed after "
        f"{attempt} attempt(s): {last_error}"
    )
    logger.error("%s", error)
    raise error
