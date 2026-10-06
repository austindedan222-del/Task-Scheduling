"""
python manage.py send_reminders

Checks every user's tasks for anything due soon or overdue and, where
the user has an email address on file, sends them a reminder email.

This command IS the "background" part of FR7 ("track impending
deadlines in the background to send email reminders") — Django itself
has no built-in scheduler, so the standard way to run something
periodically is to let the OS call this command on a timer rather than
have Django run its own background thread. See README.md for how to
schedule it with cron (Linux/macOS) or Task Scheduler (Windows).
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from scheduler.models import Task
from scheduler.services.reminder_service import (
    get_due_soon_tasks,
    get_overdue_tasks,
    send_reminder_email,
)


class Command(BaseCommand):
    help = "Emails each user a reminder of their due-soon and overdue tasks."

    def handle(self, *args, **options):
        sent_count = 0
        skipped_no_email = 0

        for user in User.objects.filter(is_active=True):
            tasks = Task.objects.filter(user=user)
            due_soon = get_due_soon_tasks(tasks)
            overdue = get_overdue_tasks(tasks)

            if not due_soon and not overdue:
                continue

            if send_reminder_email(user, due_soon, overdue):
                sent_count += 1
                self.stdout.write(f"Sent reminder to {user.username} <{user.email}>")
            else:
                skipped_no_email += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Sent {sent_count} reminder email(s); "
            f"{skipped_no_email} user(s) had tasks due but no email on file."
        ))
