from datetime import date, datetime, time, timedelta
from io import StringIO
from smtplib import SMTPDataError, SMTPRecipientsRefused
import json
import os
import tempfile
from unittest.mock import call, patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .algorithms.greedy_scheduler import compute_score, generate_schedule
from .models import Task, Workspace, WorkspaceMembership
from .services.reminder_service import (
    DAY_CAPACITY_MINUTES,
    SMTP_RETRY_DELAYS,
    EmailSendError,
    get_overloaded_days,
    send_reminder_email,
)


def make_task(user, title='Task', days_ahead=5, priority=5, duration=30, **kwargs):
    """A task due `days_ahead` days from now (fractions allowed, since
    deadlines carry a time of day). Duration is whole minutes; the task
    is assigned to its creator (a personal task unless told otherwise).
"""
    return Task.objects.create(
        user=user,
        assigned_to=user,
        title=title,
        deadline=timezone.now() + timedelta(days=days_ahead),
        priority=priority,
        duration_value=duration,
        duration_unit='minutes',
        **kwargs,
    )


def datetime_on(day, hour=0):
    """An aware datetime at `hour`:00 on the given date."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


class AuthAccessTests(TestCase):
    """Protected views must require login; public auth pages must not."""

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345!')

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_task_list_requires_login(self):
        response = self.client.get(reverse('task_list'))
        self.assertEqual(response.status_code, 302)

    def test_schedule_requires_login(self):
        response = self.client.get(reverse('schedule'))
        self.assertEqual(response.status_code, 302)

    def test_schedule_pdf_requires_login(self):
        response = self.client.get(reverse('schedule_pdf'))
        self.assertEqual(response.status_code, 302)

    def test_login_page_accessible_when_logged_out(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_register_page_accessible_when_logged_out(self):
        response = self.client.get(reverse('register'))
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_can_access_dashboard(self):
        self.client.login(username='alice', password='pw12345!')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_workspace_pages_require_login(self):
        # Every new workspace/supervision URL sits behind login_required
        # like the rest of the app — checked before any permission logic.
        ws = Workspace.objects.create(name='Private WS', created_by=self.user)
        for url in (
            reverse('workspace_list'),
            reverse('workspace_detail', args=[ws.pk]),
            reverse('workspace_invite', args=[ws.pk]),
            reverse('workspace_assign_task', args=[ws.pk]),
            reverse('workspace_team_overview', args=[ws.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('login'), response.url, url)

    def test_logout_then_protected_page_redirects_to_login(self):
        self.client.login(username='alice', password='pw12345!')
        self.client.post(reverse('logout'))
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class RegistrationTests(TestCase):
    def test_registration_creates_user_and_redirects_to_login(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser',
            'email': 'newuser@example.com',
            'password1': 'S3cur3Pass!23',
            'password2': 'S3cur3Pass!23',
        })
        self.assertTrue(User.objects.filter(username='newuser').exists())
        self.assertEqual(User.objects.get(username='newuser').email, 'newuser@example.com')
        self.assertRedirects(response, reverse('login'))

    def test_registration_rejects_mismatched_passwords(self):
        response = self.client.post(reverse('register'), {
            'username': 'baduser',
            'email': 'baduser@example.com',
            'password1': 'S3cur3Pass!23',
            'password2': 'DifferentPass!23',
        })
        self.assertFalse(User.objects.filter(username='baduser').exists())
        self.assertEqual(response.status_code, 200)

    def test_registration_rejects_duplicate_username(self):
        User.objects.create_user('dupe', password='pw12345!')
        response = self.client.post(reverse('register'), {
            'username': 'dupe',
            'email': 'dupe@example.com',
            'password1': 'S3cur3Pass!23',
            'password2': 'S3cur3Pass!23',
        })
        self.assertEqual(User.objects.filter(username='dupe').count(), 1)
        self.assertEqual(response.status_code, 200)

    def test_registration_requires_email(self):
        # FR7 (email reminders) depends on every account having an email,
        # so registration must reject a submission that omits it.
        response = self.client.post(reverse('register'), {
            'username': 'noemail',
            'password1': 'S3cur3Pass!23',
            'password2': 'S3cur3Pass!23',
        })
        self.assertFalse(User.objects.filter(username='noemail').exists())
        self.assertEqual(response.status_code, 200)


class TaskCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345!')
        self.other = User.objects.create_user('bob', password='pw12345!')
        self.client.login(username='alice', password='pw12345!')

    def test_create_task_assigns_current_user(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Write Chapter 4',
            'description': '',
            'deadline': (date.today() + timedelta(days=3)).isoformat(),
            'priority': 8,
            'duration_value': 90,
            'duration_unit': 'minutes',
        })
        self.assertRedirects(response, reverse('task_list'))
        task = Task.objects.get(title='Write Chapter 4')
        self.assertEqual(task.user, self.user)

    def test_task_list_shows_only_own_tasks(self):
        make_task(self.user, title='Mine')
        make_task(self.other, title='Not Mine')
        response = self.client.get(reverse('task_list'))
        titles = [t.title for t in response.context['tasks']]
        self.assertIn('Mine', titles)
        self.assertNotIn('Not Mine', titles)

    def test_update_task(self):
        task = make_task(self.user, title='Old Title')
        response = self.client.post(reverse('task_update', args=[task.pk]), {
            'title': 'New Title',
            'description': '',
            'deadline': task.deadline.isoformat(),
            'priority': task.priority,
            'duration_value': task.duration_value,
            'duration_unit': task.duration_unit,
        })
        self.assertRedirects(response, reverse('task_list'))
        task.refresh_from_db()
        self.assertEqual(task.title, 'New Title')

    def test_delete_task(self):
        task = make_task(self.user, title='Delete Me')
        response = self.client.post(reverse('task_delete', args=[task.pk]))
        self.assertRedirects(response, reverse('task_list'))
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_cannot_view_another_users_task_via_edit_url(self):
        other_task = make_task(self.other, title="Bob's Task")
        response = self.client.get(reverse('task_update', args=[other_task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_delete_another_users_task_via_url(self):
        other_task = make_task(self.other, title="Bob's Task")
        response = self.client.post(reverse('task_delete', args=[other_task.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Task.objects.filter(pk=other_task.pk).exists())

    def test_invalid_priority_rejected(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Bad priority',
            'description': '',
            'deadline': (date.today() + timedelta(days=1)).isoformat(),
            'priority': 11,
            'duration_value': 30,
            'duration_unit': 'minutes',
        })
        self.assertEqual(response.status_code, 200)  # form redisplayed with errors
        self.assertFalse(Task.objects.filter(title='Bad priority').exists())


class TaskAjaxTests(TestCase):
    """The same views, called the way main.js calls them (fetch() with
    the X-Requested-With header), returning JSON instead of a redirect.
    """

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345!')
        self.client.login(username='alice', password='pw12345!')
        self.ajax_headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def test_add_task_form_get_returns_json_fragment(self):
        response = self.client.get(reverse('task_create'), **self.ajax_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('html', data)
        self.assertIn('Save Task', data['html'])

    def test_create_task_ajax_success_returns_table_html(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Ajax Task',
            'description': '',
            'deadline': (date.today() + timedelta(days=3)).isoformat(),
            'priority': 6,
            'duration_value': 20,
            'duration_unit': 'minutes',
        }, **self.ajax_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('Ajax Task', data['table_html'])
        self.assertTrue(Task.objects.filter(title='Ajax Task').exists())

    def test_create_task_ajax_invalid_returns_error_json(self):
        response = self.client.post(reverse('task_create'), {
            'title': '',
            'description': '',
            'deadline': (date.today() + timedelta(days=1)).isoformat(),
            'priority': 5,
            'duration_value': 10,
            'duration_unit': 'minutes',
        }, **self.ajax_headers)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('html', data)

    def test_delete_task_ajax_get_returns_confirm_fragment(self):
        task = make_task(self.user, title='Delete Me')
        response = self.client.get(reverse('task_delete', args=[task.pk]), **self.ajax_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('Delete Me', data['html'])

    def test_delete_task_ajax_post_returns_updated_table(self):
        task = make_task(self.user, title='Delete Me')
        response = self.client.post(reverse('task_delete', args=[task.pk]), **self.ajax_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertNotIn('Delete Me', data['table_html'])
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_cannot_ajax_delete_another_users_task(self):
        other = User.objects.create_user('bob', password='pw12345!')
        other_task = make_task(other, title="Bob's Task")
        response = self.client.post(reverse('task_delete', args=[other_task.pk]), **self.ajax_headers)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Task.objects.filter(pk=other_task.pk).exists())


class GreedyAlgorithmTests(TestCase):
    def test_urgency_formula(self):
        urgency, _ = compute_score(priority=5, days_remaining=2)
        self.assertAlmostEqual(urgency, 1 / 3)

    def test_score_formula_matches_spec(self):
        # Task A: P=8, D=2 -> S ~= 5.700
        urgency_a, score_a = compute_score(priority=8, days_remaining=2)
        self.assertAlmostEqual(urgency_a, 1 / 3, places=3)
        self.assertAlmostEqual(score_a, 5.7, places=1)

        # Task B: P=10, D=15 -> S ~= 7.019
        urgency_b, score_b = compute_score(priority=10, days_remaining=15)
        self.assertAlmostEqual(urgency_b, 1 / 16, places=4)
        self.assertAlmostEqual(score_b, 7.019, places=2)

        # Task B should outrank Task A, per the spec example.
        self.assertGreater(score_b, score_a)

    def test_priority_is_not_normalized(self):
        # A priority-10 task must contribute a full 7.0 base points (0.7 * 10),
        # not a normalized fraction.
        _, score = compute_score(priority=10, days_remaining=100)
        self.assertGreaterEqual(score, 7.0)

    def test_descending_score_order(self):
        user = User.objects.create_user('carol', password='pw12345!')
        low = make_task(user, title='Low', priority=1, days_ahead=30)
        high = make_task(user, title='High', priority=10, days_ahead=30)
        scheduled = generate_schedule(Task.objects.filter(user=user))
        self.assertEqual(scheduled[0].task, high)
        self.assertEqual(scheduled[1].task, low)

    def test_tie_break_earlier_deadline_first(self):
        user = User.objects.create_user('dave', password='pw12345!')
        later = make_task(user, title='Later', priority=5, days_ahead=10, duration=30)
        earlier = make_task(user, title='Earlier', priority=5, days_ahead=3, duration=30)
        scheduled = generate_schedule(Task.objects.filter(user=user))
        # Same priority, different D -> not a true tie in score, but earlier
        # deadline carries higher urgency too, so it should still sort first.
        self.assertEqual(scheduled[0].task, earlier)

    def test_tie_break_shorter_duration_when_score_and_deadline_equal(self):
        user = User.objects.create_user('erin', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        short = Task.objects.create(user=user, assigned_to=user, title='Short', deadline=datetime_on(same_day), priority=5, duration_value=15, duration_unit='minutes')
        long = Task.objects.create(user=user, assigned_to=user, title='Long', deadline=datetime_on(same_day), priority=5, duration_value=120, duration_unit='minutes')
        scheduled = generate_schedule(Task.objects.filter(user=user))
        self.assertEqual(scheduled[0].task, short)
        self.assertEqual(scheduled[1].task, long)

    def test_overdue_task_clamped_to_zero_days(self):
        user = User.objects.create_user('frank', password='pw12345!')
        overdue = make_task(user, title='Overdue', priority=5, days_ahead=-3)
        scheduled = generate_schedule(Task.objects.filter(user=user))
        self.assertEqual(scheduled[0].days_remaining, 0)
        self.assertAlmostEqual(scheduled[0].urgency, 1.0)

    def test_priority_range_validation(self):
        user = User.objects.create_user('gail', password='pw12345!')
        task = Task(user=user, assigned_to=user, title='Bad', deadline=datetime_on(date.today()), priority=11, duration_value=10, duration_unit='minutes')
        with self.assertRaises(Exception):
            task.full_clean()


class ScheduleIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345!')
        self.other = User.objects.create_user('bob', password='pw12345!')
        self.client.login(username='alice', password='pw12345!')

    def test_schedule_page_renders(self):
        make_task(self.user, title='Mine')
        response = self.client.get(reverse('schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mine')

    def test_schedule_only_includes_own_tasks(self):
        make_task(self.user, title='Mine')
        make_task(self.other, title='Not Mine')
        response = self.client.get(reverse('schedule'))
        titles = [item.task.title for item in response.context['scheduled']]
        self.assertIn('Mine', titles)
        self.assertNotIn('Not Mine', titles)

    def test_pdf_export_works_and_returns_pdf(self):
        make_task(self.user, title='Mine')
        response = self.client.get(reverse('schedule_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_pdf_export_handles_no_tasks(self):
        response = self.client.get(reverse('schedule_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')


class OverloadWarningTests(TestCase):
    """Reliability NFR (Section 3.2.2): warn the user if more work is
    scheduled than can physically fit in a day.
    """

    def test_day_within_capacity_is_not_flagged(self):
        user = User.objects.create_user('hana', password='pw12345!')
        make_task(user, title='Short', days_ahead=5, duration=60)
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(overloaded, [])

    def test_day_over_capacity_is_flagged(self):
        user = User.objects.create_user('ivan', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(user=user, assigned_to=user, title='A', deadline=datetime_on(same_day), priority=5, duration_value=1000, duration_unit='minutes')
        Task.objects.create(user=user, assigned_to=user, title='B', deadline=datetime_on(same_day), priority=5, duration_value=1000, duration_unit='minutes')
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(len(overloaded), 1)
        self.assertEqual(overloaded[0]['date'], same_day)
        self.assertEqual(overloaded[0]['total_minutes'], 2000)
        self.assertGreater(overloaded[0]['total_minutes'], DAY_CAPACITY_MINUTES)

    def test_exactly_capacity_is_not_flagged(self):
        # 1440 minutes is a full day, not *more* than a day — boundary check.
        user = User.objects.create_user('jade', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(user=user, assigned_to=user, title='Full day', deadline=datetime_on(same_day), priority=5, duration_value=DAY_CAPACITY_MINUTES, duration_unit='minutes')
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(overloaded, [])

    def test_overload_warning_shown_on_dashboard(self):
        user = User.objects.create_user('kay', password='pw12345!')
        self.client.login(username='kay', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(user=user, assigned_to=user, title='A', deadline=datetime_on(same_day), priority=5, duration_value=1000, duration_unit='minutes')
        Task.objects.create(user=user, assigned_to=user, title='B', deadline=datetime_on(same_day), priority=5, duration_value=1000, duration_unit='minutes')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'physically fit in a day')


class EmailReminderTests(TestCase):
    """FR7 (Section 3.2.2): track impending deadlines in the background
    to send email reminders.
    """

    def setUp(self):
        self.user = User.objects.create_user('liam', password='pw12345!', email='liam@example.com')

    def test_send_reminder_email_with_due_soon_task(self):
        task = make_task(self.user, title='Due Soon Task', days_ahead=1)
        sent = send_reminder_email(self.user, due_soon=[task], overdue=[])
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['liam@example.com'])
        self.assertIn('Due Soon Task', mail.outbox[0].body)

    def test_no_email_sent_when_nothing_due(self):
        sent = send_reminder_email(self.user, due_soon=[], overdue=[])
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_email_sent_when_user_has_no_email_on_file(self):
        no_email_user = User.objects.create_user('mina', password='pw12345!')
        task = make_task(no_email_user, title='Task', days_ahead=1)
        sent = send_reminder_email(no_email_user, due_soon=[task], overdue=[])
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_reminder_now_view_sends_email(self):
        make_task(self.user, title='Due Soon Task', days_ahead=1)
        self.client.login(username='liam', password='pw12345!')
        response = self.client.post(reverse('send_reminder_now'))
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(len(mail.outbox), 1)

    def test_send_reminders_management_command(self):
        # The background process behind FR7: emails every user who has
        # tasks due soon/overdue and an email on file, in one pass.
        make_task(self.user, title='Due Soon Task', days_ahead=1)
        no_email_user = User.objects.create_user('nora', password='pw12345!')
        make_task(no_email_user, title='Another Task', days_ahead=1)
        unaffected_user = User.objects.create_user('omar', password='pw12345!', email='omar@example.com')
        make_task(unaffected_user, title='Far Off Task', days_ahead=30)

        call_command('send_reminders')

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['liam@example.com'])

    def test_reminders_go_to_every_user_who_has_an_email(self):
        """One pass of send_reminders emails each account that has an
        address on file and something due, and each message only ever
        contains that user's own tasks."""
        second = User.objects.create_user('mia', password='pw12345!', email='mia@example.com')
        make_task(self.user, title='Liams Due Task', days_ahead=1)
        make_task(second, title='Mias Due Task', days_ahead=1)

        call_command('send_reminders')

        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, ['liam@example.com', 'mia@example.com'])
        by_recipient = {m.to[0]: m.body for m in mail.outbox}
        self.assertIn('Liams Due Task', by_recipient['liam@example.com'])
        self.assertNotIn('Mias Due Task', by_recipient['liam@example.com'])
        self.assertIn('Mias Due Task', by_recipient['mia@example.com'])
        self.assertNotIn('Liams Due Task', by_recipient['mia@example.com'])

    def test_dashboard_always_offers_the_reminder_button(self):
        # The button used to render only when something was due, which
        # made the feature undiscoverable on a fresh account.
        self.client.login(username='liam', password='pw12345!')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('send_reminder_now'))

    def test_reminder_button_with_nothing_due_reports_instead_of_failing(self):
        self.client.login(username='liam', password='pw12345!')
        response = self.client.post(reverse('send_reminder_now'))
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(len(mail.outbox), 0)


class EmailFailureTests(TestCase):
    """FR7 reliability hardening: SMTP failures must be retried with
    backoff when transient, logged and skipped when permanent — never
    aborting a whole reminder run or crashing the dashboard button.
    """

    SERVICE = 'scheduler.services.reminder_service'

    def setUp(self):
        self.user = User.objects.create_user(
            'liam', password='pw12345!', email='liam@example.com')
        self.task = make_task(self.user, title='Due Soon Task', days_ahead=1)

    @patch(f'{SERVICE}.time.sleep')
    @patch(f'{SERVICE}.send_mail')
    def test_transient_failure_is_retried_with_backoff_until_success(
            self, mock_send, mock_sleep):
        # Two 4xx "try again" replies, then success: exactly 3 calls and
        # the configured backoff delays in between.
        mock_send.side_effect = [
            SMTPDataError(451, b'temporary local problem'),
            SMTPDataError(451, b'temporary local problem'),
            1,
        ]

        sent = send_reminder_email(self.user, due_soon=[self.task], overdue=[])

        self.assertTrue(sent)
        self.assertEqual(mock_send.call_count, 3)
        mock_sleep.assert_has_calls([call(d) for d in SMTP_RETRY_DELAYS])

    @patch(f'{SERVICE}.time.sleep')
    @patch(f'{SERVICE}.send_mail')
    def test_permanent_failure_fails_fast_without_retry(
            self, mock_send, mock_sleep):
        # 550 = bad recipient — retrying only wastes time.
        mock_send.side_effect = SMTPRecipientsRefused(
            {'liam@example.com': (550, b'User unknown')})

        with self.assertRaises(EmailSendError) as ctx:
            send_reminder_email(self.user, due_soon=[self.task], overdue=[])

        self.assertEqual(mock_send.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn('liam', str(ctx.exception))
        self.assertIn('liam@example.com', str(ctx.exception))

    @patch(f'{SERVICE}.time.sleep')
    @patch(f'{SERVICE}.send_mail')
    def test_failure_is_logged_with_recipient_and_error(
            self, mock_send, mock_sleep):
        mock_send.side_effect = SMTPRecipientsRefused(
            {'liam@example.com': (550, b'User unknown')})

        with self.assertLogs(self.SERVICE, level='ERROR') as logs:
            with self.assertRaises(EmailSendError):
                send_reminder_email(self.user, due_soon=[self.task], overdue=[])

        logged = '\n'.join(logs.output)
        self.assertIn('liam', logged)
        self.assertIn('liam@example.com', logged)
        self.assertIn('550', logged)

    @patch(f'{SERVICE}.time.sleep')
    @patch(f'{SERVICE}.send_mail')
    def test_send_reminders_continues_past_one_failing_recipient(
            self, mock_send, mock_sleep):
        # "One person's bad email address shouldn't stop everyone
        # else's": first recipient is refused, second must still send.
        from django.core.mail import send_mail as real_send_mail

        self.task.delete()  # isolate: only good/bad users have due work

        good = User.objects.create_user(
            'mia', password='pw12345!', email='mia@example.com')
        make_task(good, title='Mias Due Task', days_ahead=1)
        bad = User.objects.create_user(
            'bob', password='pw12345!', email='bob@example.com')
        make_task(bad, title='Bobs Due Task', days_ahead=1)

        def selective_send(*args, **kwargs):
            if kwargs['recipient_list'] == ['bob@example.com']:
                raise SMTPRecipientsRefused(
                    {'bob@example.com': (550, b'User unknown')})
            return real_send_mail(*args, **kwargs)

        mock_send.side_effect = selective_send

        stdout, stderr = StringIO(), StringIO()
        # Must complete — not raise, not exit early.
        call_command('send_reminders', stdout=stdout, stderr=stderr)

        recipients = [m.to[0] for m in mail.outbox]
        self.assertEqual(recipients, ['mia@example.com'])
        output = stderr.getvalue() + stdout.getvalue()
        self.assertIn('FAILED', output)
        self.assertIn('bob@example.com', output)
        self.assertIn('Sent 1 reminder email(s); 1 failed;', output)

    @patch(f'{SERVICE}.time.sleep')
    @patch(f'{SERVICE}.send_mail')
    def test_dashboard_button_reports_failure_instead_of_crashing(
            self, mock_send, mock_sleep):
        mock_send.side_effect = SMTPDataError(454, b'temporary auth failure')
        self.client.login(username='liam', password='pw12345!')

        response = self.client.post(reverse('send_reminder_now'))

        # assertRedirects would itself GET the dashboard and consume
        # the one-shot message, so check the redirect manually first.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))
        followed = self.client.get(reverse('dashboard'))
        self.assertContains(followed, 'Could not send the reminder email')
        self.assertEqual(len(mail.outbox), 0)


class DataMigrationDrillTests(TestCase):
    """The tested SQLite → PostgreSQL migration path (README section
    "Migrating from SQLite to PostgreSQL").

    PostgreSQL itself is exercised by the documented drill; what these
    tests lock down is the half that silently rots — the exact dumpdata
    invocation the README tells students to run. If it ever starts
    dumping contenttypes/auth.permission again, the load step dies with
    a duplicate-key IntegrityError on the target database, which is
    precisely the pitfall the README's troubleshooting note describes.
    """

    # Exactly what README.md tells the student to type.
    README_EXCLUDES = ['contenttypes', 'auth.permission', 'sessions']

    def _seed(self):
        """One of every row this project owns: two users, a workspace,
        two memberships and a task whose assignee differs from its
        creator (so the dump carries a task -> *other* user FK)."""
        owner = User.objects.create_user(
            'dumper', password='pw12345!', email='dumper@example.com')
        member = User.objects.create_user(
            'dumpee', password='pw12345!', email='dumpee@example.com')
        workspace = Workspace.objects.create(
            name='Round Trip', created_by=owner)
        WorkspaceMembership.objects.create(
            workspace=workspace, user=owner,
            role=WorkspaceMembership.ROLE_SUPERVISOR)
        WorkspaceMembership.objects.create(
            workspace=workspace, user=member,
            role=WorkspaceMembership.ROLE_MEMBER)
        task = make_task(owner, title='Round Trip Task', days_ahead=1,
                         workspace=workspace)
        # make_task assigns to its creator (passing assigned_to= would
        # collide with that); repoint via UPDATE for a cross-user FK.
        Task.objects.filter(pk=task.pk).update(assigned_to=member)
        return owner, member, workspace

    def _dump(self, excludes):
        """Run the README dumpdata command into a fresh temp file."""
        tmp_dir = tempfile.mkdtemp(prefix='dumpdrill_')
        path = os.path.join(tmp_dir, 'db_dump.json')
        call_command('dumpdata', exclude=excludes, output=path, verbosity=0)
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)

    def test_readme_dump_excludes_exactly_what_migrate_regenerates(self):
        # dumpdata serialises rows, not schemas — seed first so "the
        # documented excludes still keep the real data" is a claim about
        # data that exists.
        self._seed()
        # A no-excludes dump (what a student gets by guessing) contains
        # content types — loading THAT into an already-migrated database
        # is the classic duplicate-key failure. The documented excludes
        # must keep them out.
        naive = self._dump(excludes=[])
        naive_models = {obj['model'] for obj in naive}
        self.assertIn('contenttypes.contenttype', naive_models)

        documented = self._dump(excludes=self.README_EXCLUDES)
        models = {obj['model'] for obj in documented}
        self.assertNotIn('contenttypes.contenttype', models)
        self.assertNotIn('auth.permission', models)
        self.assertNotIn('sessions.session', models)
        # The data that actually matters must survive the excludes.
        self.assertIn('auth.user', models)
        self.assertIn('scheduler.task', models)
        self.assertIn('scheduler.workspace', models)
        self.assertIn('scheduler.workspacemembership', models)

    def test_dump_load_round_trip_restores_everything(self):
        # Seed data covering every table the project owns, incl. a
        # password hash (must still verify after the round trip).
        self._seed()

        dump = self._dump(excludes=self.README_EXCLUDES)
        self.assertTrue(any(o['model'] == 'auth.user' for o in dump))

        # Wipe everything the dump covers — users cascade to tasks,
        # workspaces and memberships, so the load must rebuild all of it.
        User.objects.all().delete()
        Workspace.objects.all().delete()
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(WorkspaceMembership.objects.count(), 0)

        tmp_dir = tempfile.mkdtemp(prefix='dumpdrill_')
        path = os.path.join(tmp_dir, 'db_dump.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(dump, fh)
        call_command('loaddata', path, verbosity=0)

        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(WorkspaceMembership.objects.count(), 2)
        restored = User.objects.get(username='dumper')
        self.assertEqual(restored.email, 'dumper@example.com')
        self.assertTrue(restored.check_password('pw12345!'))
        task = Task.objects.get()
        self.assertEqual(task.title, 'Round Trip Task')
        self.assertEqual(task.assigned_to.username, 'dumpee')
        self.assertEqual(task.workspace.name, 'Round Trip')
        self.assertEqual(
            task.workspace.memberships.count(), 2)


class HelpPageTests(TestCase):
    """The how-to guide must be reachable from the nav, logged in or out."""

    def test_help_page_publicly_accessible(self):
        response = self.client.get(reverse('help'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Help &amp; User Guide')

    def test_help_page_reachable_from_sidebar_when_logged_in(self):
        User.objects.create_user('hana2', password='pw12345!')
        self.client.login(username='hana2', password='pw12345!')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('help'))

    def test_help_page_documents_email_reminders(self):
        response = self.client.get(reverse('help'))
        self.assertContains(response, 'Email My Reminders Now')


class FlexibleTimeSpanTests(TestCase):
    """Deadlines carry a time of day, durations are value+unit pairs
    normalized to minutes, and the algorithm's D input is fractional.
    The scoring formula itself is untouched.
    """

    def setUp(self):
        self.user = User.objects.create_user('tomas', password='pw12345!')
        self.client.login(username='tomas', password='pw12345!')

    def test_deadline_stores_time_of_day(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Evening deadline',
            'description': '',
            'deadline': '2026-12-24T18:30',
            'priority': 6,
            'duration_value': 2,
            'duration_unit': 'hours',
        })
        self.assertRedirects(response, reverse('task_list'))
        task = Task.objects.get(title='Evening deadline')
        # Read back in the active time zone: the instant stored is UTC.
        local = timezone.localtime(task.deadline)
        self.assertEqual((local.hour, local.minute), (18, 30))

    def test_date_only_deadline_still_accepted_as_midnight(self):
        # Back-compat: a plain YYYY-MM-DD value means midnight that day.
        response = self.client.post(reverse('task_create'), {
            'title': 'Date only',
            'description': '',
            'deadline': (date.today() + timedelta(days=3)).isoformat(),
            'priority': 4,
            'duration_value': 45,
            'duration_unit': 'minutes',
        })
        self.assertRedirects(response, reverse('task_list'))
        task = Task.objects.get(title='Date only')
        local = timezone.localtime(task.deadline)
        self.assertEqual(local.hour, 0)
        self.assertEqual(local.date(), date.today() + timedelta(days=3))

    def test_hour_level_deadline_gets_fractional_days(self):
        ref = timezone.now()
        make_task(self.user, title='Three hours out', days_ahead=0.125)
        scheduled = generate_schedule(
            Task.objects.filter(user=self.user), reference_date=ref,
        )
        self.assertAlmostEqual(scheduled[0].days_remaining, 0.125, places=3)
        self.assertAlmostEqual(scheduled[0].urgency, 1 / 1.125, places=3)

    def test_hour_deadline_outranks_day_deadline_at_equal_priority(self):
        ref = timezone.now()
        near = make_task(self.user, title='3 hours', days_ahead=0.125, priority=7)
        far = make_task(self.user, title='5 days', days_ahead=5, priority=7)
        scheduled = generate_schedule(
            Task.objects.filter(user=self.user), reference_date=ref,
        )
        self.assertEqual([item.task for item in scheduled], [near, far])

    def test_past_time_of_day_clamps_to_zero_days(self):
        ref = timezone.now()
        Task.objects.create(
            user=self.user, assigned_to=self.user, title='Two hours ago',
            deadline=ref - timedelta(hours=2),
            priority=5, duration_value=30, duration_unit='minutes',
        )
        scheduled = generate_schedule(
            Task.objects.filter(user=self.user), reference_date=ref,
        )
        self.assertEqual(scheduled[0].days_remaining, 0)
        self.assertEqual(scheduled[0].urgency, 1.0)

    def test_month_level_deadline_days_are_fractional_too(self):
        ref = timezone.now()
        make_task(self.user, title='A month out', days_ahead=30)
        scheduled = generate_schedule(
            Task.objects.filter(user=self.user), reference_date=ref,
        )
        self.assertAlmostEqual(scheduled[0].days_remaining, 30, places=3)
        self.assertAlmostEqual(scheduled[0].urgency, 1 / 31, places=4)

    def test_duration_units_normalize_to_minutes(self):
        cases = {
            'minutes': 1,
            'hours': 60,
            'days': 1440,
            'weeks': 7 * 1440,
            'months': 30 * 1440,
        }
        for unit, minutes in cases.items():
            task = Task(
                user=self.user, title=f'1 {unit}',
                deadline=datetime_on(date.today() + timedelta(days=2)),
                priority=5, duration_value=1, duration_unit=unit,
            )
            self.assertEqual(task.duration_minutes, minutes, unit)

    def test_overload_check_uses_normalized_minutes(self):
        # 22 hours + 150 minutes = 1470 min > 1440, so the day IS
        # overloaded. Raw values (22 + 150) would never trip the check,
        # which is exactly why normalization matters.
        user = User.objects.create_user('hana3', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(
            user=user, assigned_to=user, title='Long block', deadline=datetime_on(same_day),
            priority=5, duration_value=22, duration_unit='hours',
        )
        Task.objects.create(
            user=user, assigned_to=user, title='Extra', deadline=datetime_on(same_day),
            priority=5, duration_value=150, duration_unit='minutes',
        )
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(len(overloaded), 1)
        self.assertEqual(overloaded[0]['total_minutes'], 1470)

    def test_tie_break_compares_normalized_minutes(self):
        # Same score and same deadline -> shorter duration wins. "2 hours"
        # (120 min) must lose to "30 minutes", not beat it the way the raw
        # values 2 < 30 would.
        user = User.objects.create_user('nina', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        long = Task.objects.create(
            user=user, assigned_to=user, title='Two hours', deadline=datetime_on(same_day),
            priority=5, duration_value=2, duration_unit='hours',
        )
        short = Task.objects.create(
            user=user, assigned_to=user, title='Thirty minutes', deadline=datetime_on(same_day),
            priority=5, duration_value=30, duration_unit='minutes',
        )
        scheduled = generate_schedule(Task.objects.filter(user=user))
        self.assertEqual([item.task for item in scheduled], [short, long])


class ProgressTrackingTests(TestCase):
    """Status + percent_complete on Task, the badge/progress UI, and
    completed tasks being excluded from the generated schedule.
    """

    def setUp(self):
        self.user = User.objects.create_user('pia', password='pw12345!')
        self.client.login(username='pia', password='pw12345!')

    def test_status_defaults_to_not_started_via_form_and_model(self):
        self.client.post(reverse('task_create'), {
            'title': 'Fresh task',
            'description': '',
            'deadline': (date.today() + timedelta(days=2)).isoformat(),
            'priority': 5,
            'duration_value': 30,
            'duration_unit': 'minutes',
            # status / percent_complete deliberately omitted
        })
        task = Task.objects.get(title='Fresh task')
        self.assertEqual(task.status, 'not_started')
        self.assertEqual(task.percent_complete, 0)

    def test_percent_complete_rejects_out_of_range(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Bad percent',
            'description': '',
            'deadline': (date.today() + timedelta(days=2)).isoformat(),
            'priority': 5,
            'duration_value': 30,
            'duration_unit': 'minutes',
            'status': 'in_progress',
            'percent_complete': 150,
        })
        self.assertEqual(response.status_code, 200)  # redisplayed with errors
        self.assertFalse(Task.objects.filter(title='Bad percent').exists())

    def test_percent_complete_accepts_boundary_values(self):
        self.client.post(reverse('task_create'), {
            'title': 'Boundary',
            'description': '',
            'deadline': (date.today() + timedelta(days=2)).isoformat(),
            'priority': 5,
            'duration_value': 30,
            'duration_unit': 'minutes',
            'status': 'in_progress',
            'percent_complete': 100,
        })
        task = Task.objects.get(title='Boundary')
        self.assertEqual(task.percent_complete, 100)
        self.assertEqual(task.status, 'in_progress')

    def test_task_list_renders_status_badge_and_progress_bar(self):
        task = make_task(self.user, title='In flight')
        task.status = 'in_progress'
        task.percent_complete = 60
        task.save()
        response = self.client.get(reverse('task_list'))
        self.assertContains(response, 'status-badge status-in_progress')
        self.assertContains(response, 'progress-fill')
        self.assertContains(response, '60%')

    def test_member_can_update_status_and_percent_via_edit_form(self):
        task = make_task(self.user, title='Update me')
        response = self.client.post(reverse('task_update', args=[task.pk]), {
            'title': 'Update me',
            'description': '',
            'deadline': task.deadline.isoformat(),
            'priority': task.priority,
            'duration_value': task.duration_value,
            'duration_unit': task.duration_unit,
            'status': 'in_progress',
            'percent_complete': 40,
        })
        self.assertRedirects(response, reverse('task_list'))
        task.refresh_from_db()
        self.assertEqual(task.status, 'in_progress')
        self.assertEqual(task.percent_complete, 40)

    def test_completed_tasks_excluded_from_generated_schedule(self):
        make_task(self.user, title='Still open')
        done = make_task(self.user, title='All done')
        done.status = 'completed'
        done.save()
        response = self.client.get(reverse('schedule'))
        titles = [item.task.title for item in response.context['scheduled']]
        self.assertIn('Still open', titles)
        self.assertNotIn('All done', titles)

    def test_completed_task_not_flagged_as_due_soon(self):
        make_task(self.user, title='Finished work', days_ahead=1)
        Task.objects.filter(title='Finished work').update(status='completed')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(list(response.context['due_soon']), [])

    def test_completed_task_no_longer_overloads_a_day(self):
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(
            user=self.user, assigned_to=self.user, title='Huge but done', deadline=datetime_on(same_day),
            priority=5, duration_value=2000, duration_unit='minutes',
            status='completed',
        )
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['overloaded_days'], [])


class WorkspaceTests(TestCase):
    """#1: workspaces, memberships, invites — and that a user only ever
    sees workspaces they belong to."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', password='pw12345!', email='alice@example.com')
        self.bob = User.objects.create_user('bob', password='pw12345!', email='bob@example.com')
        self.client.login(username='alice', password='pw12345!')

    def _make_workspace(self, name, creator, creator_role=WorkspaceMembership.ROLE_SUPERVISOR):
        workspace = Workspace.objects.create(name=name, created_by=creator)
        WorkspaceMembership.objects.create(
            workspace=workspace, user=creator, role=creator_role,
        )
        return workspace

    def test_create_workspace_makes_creator_the_supervisor(self):
        response = self.client.post(reverse('workspace_list'), {'name': 'Capstone Team'})
        workspace = Workspace.objects.get(name='Capstone Team')
        self.assertRedirects(response, reverse('workspace_detail', args=[workspace.pk]))
        self.assertEqual(workspace.created_by, self.alice)
        membership = WorkspaceMembership.objects.get(workspace=workspace, user=self.alice)
        self.assertEqual(membership.role, WorkspaceMembership.ROLE_SUPERVISOR)

    def test_workspace_list_shows_only_my_workspaces(self):
        self._make_workspace('Alices WS', self.alice)
        self._make_workspace('Bobs WS', self.bob)
        response = self.client.get(reverse('workspace_list'))
        self.assertContains(response, 'Alices WS')
        self.assertNotContains(response, 'Bobs WS')

    def test_workspace_detail_404_for_non_member(self):
        workspace = self._make_workspace('Bobs WS', self.bob)
        response = self.client.get(reverse('workspace_detail', args=[workspace.pk]))
        self.assertEqual(response.status_code, 404)

    def test_invite_member_by_username(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        response = self.client.post(
            reverse('workspace_invite', args=[workspace.pk]),
            {'identifier': 'bob', 'role': WorkspaceMembership.ROLE_MEMBER},
        )
        self.assertRedirects(response, reverse('workspace_detail', args=[workspace.pk]))
        membership = WorkspaceMembership.objects.get(workspace=workspace, user=self.bob)
        self.assertEqual(membership.role, WorkspaceMembership.ROLE_MEMBER)

    def test_invite_member_by_email_is_case_insensitive(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        response = self.client.post(
            reverse('workspace_invite', args=[workspace.pk]),
            {'identifier': 'BOB@Example.com', 'role': WorkspaceMembership.ROLE_MEMBER},
        )
        self.assertRedirects(response, reverse('workspace_detail', args=[workspace.pk]))
        self.assertTrue(
            WorkspaceMembership.objects.filter(workspace=workspace, user=self.bob).exists(),
        )

    def test_invite_unknown_identifier_shows_form_error(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        response = self.client.post(
            reverse('workspace_invite', args=[workspace.pk]),
            {'identifier': 'ghost', 'role': WorkspaceMembership.ROLE_MEMBER},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No account matches')
        self.assertEqual(
            WorkspaceMembership.objects.filter(workspace=workspace).count(), 1,
        )  # only the creator remains

    def test_invite_duplicate_member_is_rejected(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        invite = {'identifier': 'bob', 'role': WorkspaceMembership.ROLE_MEMBER}
        self.client.post(reverse('workspace_invite', args=[workspace.pk]), invite)
        response = self.client.post(reverse('workspace_invite', args=[workspace.pk]), invite)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already a member')
        self.assertEqual(
            WorkspaceMembership.objects.filter(workspace=workspace, user=self.bob).count(),
            1,
        )

    def test_member_cannot_invite_others(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        WorkspaceMembership.objects.create(
            workspace=workspace, user=self.bob, role=WorkspaceMembership.ROLE_MEMBER,
        )
        charlie = User.objects.create_user('charlie', password='pw12345!')
        self.client.login(username='bob', password='pw12345!')
        response = self.client.post(
            reverse('workspace_invite', args=[workspace.pk]),
            {'identifier': 'charlie', 'role': WorkspaceMembership.ROLE_MEMBER},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            WorkspaceMembership.objects.filter(workspace=workspace, user=charlie).exists(),
        )

    def test_user_can_hold_different_roles_in_different_workspaces(self):
        ws1 = self._make_workspace('Alices WS', self.alice)      # alice = supervisor
        ws2 = self._make_workspace('Bobs WS', self.bob)          # bob = supervisor
        WorkspaceMembership.objects.create(
            workspace=ws2, user=self.alice, role=WorkspaceMembership.ROLE_MEMBER,
        )
        role1 = WorkspaceMembership.objects.get(workspace=ws1, user=self.alice).role
        role2 = WorkspaceMembership.objects.get(workspace=ws2, user=self.alice).role
        self.assertEqual(role1, WorkspaceMembership.ROLE_SUPERVISOR)
        self.assertEqual(role2, WorkspaceMembership.ROLE_MEMBER)

    def test_membership_unique_per_workspace_and_user(self):
        workspace = self._make_workspace('Alices WS', self.alice)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkspaceMembership.objects.create(
                    workspace=workspace, user=self.alice,
                    role=WorkspaceMembership.ROLE_MEMBER,
                )

    def test_new_tasks_are_personal_by_default(self):
        self.client.post(reverse('task_create'), {
            'title': 'Just mine',
            'description': '',
            'deadline': (date.today() + timedelta(days=2)).isoformat(),
            'priority': 5,
            'duration_value': 30,
            'duration_unit': 'minutes',
        })
        task = Task.objects.get(title='Just mine')
        self.assertIsNone(task.workspace)   # personal: private forever
        self.assertEqual(task.assigned_to, self.alice)
        self.assertIsNone(task.assigned_by)


class SupervisionTests(TestCase):
    """#3: assignment, oversight, and the server-side permission rules —
    above all the privacy rule that personal (workspace=None) tasks are
    invisible to supervisors everywhere."""

    def setUp(self):
        self.sup = User.objects.create_user('sam', password='pw12345!', email='sam@example.com')
        self.mia = User.objects.create_user('mia', password='pw12345!', email='mia@example.com')
        self.olly = User.objects.create_user('olly', password='pw12345!', email='olly@example.com')
        self.ws = Workspace.objects.create(name='Lab', created_by=self.sup)
        WorkspaceMembership.objects.create(
            workspace=self.ws, user=self.sup, role=WorkspaceMembership.ROLE_SUPERVISOR,
        )
        WorkspaceMembership.objects.create(
            workspace=self.ws, user=self.mia, role=WorkspaceMembership.ROLE_MEMBER,
        )

    def _assign(self, assigned_to=None, title='Group task', **extra):
        data = {
            'title': title,
            'description': '',
            'deadline': (date.today() + timedelta(days=2)).isoformat(),
            'priority': 7,
            'duration_value': 90,
            'duration_unit': 'minutes',
            'assigned_to': (assigned_to or self.mia).pk,
        }
        data.update(extra)
        return self.client.post(
            reverse('workspace_assign_task', args=[self.ws.pk]), data,
        )

    def test_supervisor_assigns_task_to_member(self):
        self.client.login(username='sam', password='pw12345!')
        response = self._assign()
        self.assertRedirects(
            response,
            reverse('workspace_team_overview', args=[self.ws.pk]),
        )
        task = Task.objects.get(title='Group task')
        self.assertEqual(task.workspace, self.ws)
        self.assertEqual(task.assigned_to, self.mia)
        self.assertEqual(task.assigned_by, self.sup)
        self.assertEqual(task.user, self.sup)  # creator stays recorded

    def test_assign_form_renders_member_picker_for_supervisor(self):
        self.client.login(username='sam', password='pw12345!')
        response = self.client.get(reverse('workspace_assign_task', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Assign to')
        self.assertContains(response, 'mia')

    def test_member_cannot_open_assign_form(self):
        self.client.login(username='mia', password='pw12345!')
        response = self.client.get(reverse('workspace_assign_task', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_open_assign_form(self):
        self.client.login(username='olly', password='pw12345!')
        response = self.client.get(reverse('workspace_assign_task', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_assign_to_a_non_member(self):
        self.client.login(username='sam', password='pw12345!')
        response = self._assign(assigned_to=self.olly, title='Outside scope')
        self.assertEqual(response.status_code, 200)  # form redisplayed with errors
        self.assertFalse(Task.objects.filter(title='Outside scope').exists())

    def test_assigned_task_appears_only_in_assignees_task_list(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign()
        self.client.login(username='mia', password='pw12345!')
        mia_list = self.client.get(reverse('task_list'))
        self.assertContains(mia_list, 'Group task')
        self.client.login(username='sam', password='pw12345!')
        sam_list = self.client.get(reverse('task_list'))
        self.assertNotContains(sam_list, 'Group task')  # it is not Sams task

    def test_schedules_are_never_merged_across_users(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign(title='Mias assignment')
        make_task(self.sup, title='Sams personal')

        self.client.login(username='mia', password='pw12345!')
        mia_titles = [
            item.task.title
            for item in self.client.get(reverse('schedule')).context['scheduled']
        ]
        self.assertIn('Mias assignment', mia_titles)
        self.assertNotIn('Sams personal', mia_titles)

        self.client.login(username='sam', password='pw12345!')
        sam_titles = [
            item.task.title
            for item in self.client.get(reverse('schedule')).context['scheduled']
        ]
        self.assertIn('Sams personal', sam_titles)
        self.assertNotIn('Mias assignment', sam_titles)

    def test_team_overview_lists_workspace_tasks_but_never_personal_ones(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign(title='Group task')
        make_task(self.mia, title='Mias personal task')   # workspace=None
        make_task(self.sup, title='Sams personal task')   # workspace=None

        response = self.client.get(reverse('workspace_team_overview', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Group task')
        self.assertNotContains(response, 'Mias personal task')
        self.assertNotContains(response, 'Sams personal task')

    def test_member_cannot_open_team_overview(self):
        self.client.login(username='mia', password='pw12345!')
        response = self.client.get(reverse('workspace_team_overview', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_open_team_overview(self):
        self.client.login(username='olly', password='pw12345!')
        response = self.client.get(reverse('workspace_team_overview', args=[self.ws.pk]))
        self.assertEqual(response.status_code, 404)

    def test_supervisor_cannot_touch_a_members_personal_task(self):
        personal = make_task(self.mia, title='Private diary entry')
        self.client.login(username='sam', password='pw12345!')
        self.assertEqual(
            self.client.get(reverse('task_update', args=[personal.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse('task_delete', args=[personal.pk])).status_code,
            404,
        )
        self.assertTrue(Task.objects.filter(pk=personal.pk).exists())

    def test_member_updates_status_on_their_assigned_task(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign(title='Group task')
        task = Task.objects.get(title='Group task')

        self.client.login(username='mia', password='pw12345!')
        response = self.client.post(reverse('task_update', args=[task.pk]), {
            'title': 'Group task',
            'description': '',
            'deadline': task.deadline.isoformat(),
            'priority': task.priority,
            'duration_value': task.duration_value,
            'duration_unit': task.duration_unit,
            'status': 'in_progress',
            'percent_complete': 35,
        })
        self.assertRedirects(response, reverse('task_list'))
        task.refresh_from_db()
        self.assertEqual(task.status, 'in_progress')
        self.assertEqual(task.percent_complete, 35)
        self.assertEqual(task.assigned_to, self.mia)  # unchanged

    def test_member_cannot_edit_another_members_task(self):
        rex = User.objects.create_user('rex', password='pw12345!')
        WorkspaceMembership.objects.create(
            workspace=self.ws, user=rex, role=WorkspaceMembership.ROLE_MEMBER,
        )
        self.client.login(username='sam', password='pw12345!')
        self._assign(assigned_to=rex, title='Rexs task')
        rex_task = Task.objects.get(title='Rexs task')

        self.client.login(username='mia', password='pw12345!')
        response = self.client.get(reverse('task_update', args=[rex_task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_reassign_via_the_edit_form(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign(title='Group task')
        task = Task.objects.get(title='Group task')

        self.client.login(username='mia', password='pw12345!')
        self.client.post(reverse('task_update', args=[task.pk]), {
            'title': 'Group task',
            'description': '',
            'deadline': task.deadline.isoformat(),
            'priority': task.priority,
            'duration_value': task.duration_value,
            'duration_unit': task.duration_unit,
            'assigned_to': self.sup.pk,   # hostile reassignment attempt
        })
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, self.mia)

    def test_supervisor_can_reassign_a_workspace_task(self):
        self.client.login(username='sam', password='pw12345!')
        self._assign(title='Group task')
        task = Task.objects.get(title='Group task')

        response = self.client.post(reverse('task_update', args=[task.pk]), {
            'title': 'Group task',
            'description': '',
            'deadline': task.deadline.isoformat(),
            'priority': task.priority,
            'duration_value': task.duration_value,
            'duration_unit': task.duration_unit,
            'assigned_to': self.sup.pk,
        })
        self.assertRedirects(response, reverse('task_list'))
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, self.sup)

    def test_member_cannot_delete_workspace_task_assigned_to_other(self):
        rex = User.objects.create_user('rex2', password='pw12345!')
        WorkspaceMembership.objects.create(
            workspace=self.ws, user=rex, role=WorkspaceMembership.ROLE_MEMBER,
        )
        self.client.login(username='sam', password='pw12345!')
        self._assign(assigned_to=rex, title='Rexs task')
        rex_task = Task.objects.get(title='Rexs task')

        self.client.login(username='mia', password='pw12345!')
        response = self.client.post(reverse('task_delete', args=[rex_task.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Task.objects.filter(pk=rex_task.pk).exists())


class WorkspaceManagementTests(TestCase):
    """Member removal + workspace management (rename, roles, leaving,
    deletion) — every rule enforced server-side, never by hidden links."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', password='pw12345!', email='alice@example.com')
        self.bob = User.objects.create_user('bob', password='pw12345!', email='bob@example.com')
        self.carol = User.objects.create_user('carol', password='pw12345!', email='carol@example.com')
        self.ws = Workspace.objects.create(name='Capstone', created_by=self.alice)
        self.alice_membership = WorkspaceMembership.objects.create(
            workspace=self.ws, user=self.alice, role=WorkspaceMembership.ROLE_SUPERVISOR,
        )
        self.bob_membership = WorkspaceMembership.objects.create(
            workspace=self.ws, user=self.bob, role=WorkspaceMembership.ROLE_MEMBER,
        )
        self.client.login(username='alice', password='pw12345!')

    def _url(self, name, *extra):
        return reverse(name, args=[self.ws.pk, *extra])

    # -- rename (settings) -------------------------------------------------

    def test_supervisor_can_rename_workspace(self):
        response = self.client.post(self._url('workspace_settings'), {'name': 'New Name'})
        self.assertRedirects(response, self._url('workspace_detail'))
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.name, 'New Name')

    def test_rename_form_rejects_blank_name(self):
        response = self.client.post(self._url('workspace_settings'), {'name': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.name, 'Capstone')

    def test_member_cannot_open_settings(self):
        self.client.login(username='bob', password='pw12345!')
        response = self.client.get(self._url('workspace_settings'))
        self.assertEqual(response.status_code, 403)

    def test_settings_404_for_non_member(self):
        self.client.login(username='carol', password='pw12345!')
        response = self.client.get(self._url('workspace_settings'))
        self.assertEqual(response.status_code, 404)

    # -- member removal ----------------------------------------------------

    def test_supervisor_can_remove_member(self):
        response = self.client.post(self._url('workspace_remove_member', self.bob_membership.pk))
        self.assertRedirects(response, self._url('workspace_detail'))
        self.assertFalse(
            WorkspaceMembership.objects.filter(pk=self.bob_membership.pk).exists(),
        )

    def test_removed_member_loses_all_workspace_access(self):
        self.client.post(self._url('workspace_remove_member', self.bob_membership.pk))
        self.client.login(username='bob', password='pw12345!')
        self.assertEqual(self.client.get(self._url('workspace_detail')).status_code, 404)
        self.assertEqual(self.client.get(self._url('workspace_team_overview')).status_code, 404)

    def test_removed_members_tasks_are_handed_to_the_remover(self):
        ws_task = make_task(self.bob, title='Bobs ws task', workspace=self.ws)
        personal = make_task(self.bob, title='Bobs personal task')

        self.client.post(self._url('workspace_remove_member', self.bob_membership.pk))

        ws_task.refresh_from_db()
        self.assertEqual(ws_task.assigned_to, self.alice)   # stays in the workspace
        self.assertEqual(ws_task.workspace, self.ws)
        personal.refresh_from_db()
        self.assertEqual(personal.assigned_to, self.bob)    # personal tasks untouched
        self.assertIsNone(personal.workspace)

    def test_member_cannot_remove_others(self):
        self.client.login(username='bob', password='pw12345!')
        response = self.client.post(self._url('workspace_remove_member', self.alice_membership.pk))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.alice_membership.pk).exists(),
        )

    def test_remove_404_for_non_member(self):
        self.client.login(username='carol', password='pw12345!')
        response = self.client.post(self._url('workspace_remove_member', self.bob_membership.pk))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.bob_membership.pk).exists(),
        )

    def test_cannot_remove_yourself_use_leave_instead(self):
        response = self.client.post(
            self._url('workspace_remove_member', self.alice_membership.pk), follow=True,
        )
        self.assertContains(response, 'cannot remove yourself')
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.alice_membership.pk).exists(),
        )

    def test_remove_is_post_only(self):
        response = self.client.get(self._url('workspace_remove_member', self.bob_membership.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self._url('workspace_detail'))
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.bob_membership.pk).exists(),
        )

    # -- role changes ------------------------------------------------------

    def test_supervisor_can_promote_a_member(self):
        response = self.client.post(
            self._url('workspace_change_role', self.bob_membership.pk),
            {'role': WorkspaceMembership.ROLE_SUPERVISOR},
        )
        self.assertRedirects(response, self._url('workspace_detail'))
        self.bob_membership.refresh_from_db()
        self.assertEqual(self.bob_membership.role, WorkspaceMembership.ROLE_SUPERVISOR)

    def test_demoting_the_last_supervisor_is_blocked(self):
        response = self.client.post(
            self._url('workspace_change_role', self.alice_membership.pk),
            {'role': WorkspaceMembership.ROLE_MEMBER}, follow=True,
        )
        self.assertContains(response, 'no supervisor')
        self.alice_membership.refresh_from_db()
        self.assertEqual(self.alice_membership.role, WorkspaceMembership.ROLE_SUPERVISOR)

    def test_role_change_rejects_unknown_role(self):
        response = self.client.post(
            self._url('workspace_change_role', self.bob_membership.pk),
            {'role': 'wizard'}, follow=True,
        )
        self.assertContains(response, 'Unknown role')
        self.bob_membership.refresh_from_db()
        self.assertEqual(self.bob_membership.role, WorkspaceMembership.ROLE_MEMBER)

    def test_member_cannot_change_roles(self):
        self.client.login(username='bob', password='pw12345!')
        response = self.client.post(
            self._url('workspace_change_role', self.alice_membership.pk),
            {'role': WorkspaceMembership.ROLE_MEMBER},
        )
        self.assertEqual(response.status_code, 403)
        self.alice_membership.refresh_from_db()
        self.assertEqual(self.alice_membership.role, WorkspaceMembership.ROLE_SUPERVISOR)

    # -- leaving -----------------------------------------------------------

    def test_member_can_leave_and_hands_their_tasks_over(self):
        ws_task = make_task(self.bob, title='Bobs ws task', workspace=self.ws)
        self.client.login(username='bob', password='pw12345!')

        response = self.client.post(self._url('workspace_leave'), follow=True)

        self.assertRedirects(response, reverse('workspace_list'))
        self.assertFalse(
            WorkspaceMembership.objects.filter(pk=self.bob_membership.pk).exists(),
        )
        ws_task.refresh_from_db()
        self.assertEqual(ws_task.assigned_to, self.alice)   # handed to the supervisor
        self.assertContains(response, 'handed to alice')

    def test_last_supervisor_cannot_leave(self):
        response = self.client.post(self._url('workspace_leave'), follow=True)
        self.assertContains(response, 'only supervisor')
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.alice_membership.pk).exists(),
        )

    def test_a_second_supervisor_may_leave(self):
        self.client.post(
            self._url('workspace_change_role', self.bob_membership.pk),
            {'role': WorkspaceMembership.ROLE_SUPERVISOR},
        )
        alice_ws_task = make_task(self.alice, title='Alices ws task', workspace=self.ws)

        response = self.client.post(self._url('workspace_leave'), follow=True)

        self.assertRedirects(response, reverse('workspace_list'))
        self.assertFalse(
            WorkspaceMembership.objects.filter(pk=self.alice_membership.pk).exists(),
        )
        alice_ws_task.refresh_from_db()
        self.assertEqual(alice_ws_task.assigned_to, self.bob)

    def test_leave_is_post_only(self):
        response = self.client.get(self._url('workspace_leave'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self._url('workspace_detail'))
        self.assertTrue(
            WorkspaceMembership.objects.filter(pk=self.alice_membership.pk).exists(),
        )

    # -- deletion ----------------------------------------------------------

    def test_supervisor_can_delete_workspace_with_confirmation(self):
        make_task(self.bob, title='Bobs ws task', workspace=self.ws)
        personal = make_task(self.bob, title='Bobs personal task')

        confirm = self.client.get(self._url('workspace_delete'))
        self.assertContains(confirm, 'Bobs ws task')          # states what goes
        self.assertContains(confirm, 'Yes, Delete Workspace')

        response = self.client.post(self._url('workspace_delete'))
        self.assertRedirects(response, reverse('workspace_list'))
        self.assertFalse(Workspace.objects.filter(pk=self.ws.pk).exists())
        self.assertFalse(WorkspaceMembership.objects.filter(workspace_id=self.ws.pk).exists())
        self.assertFalse(Task.objects.filter(title='Bobs ws task').exists())
        self.assertTrue(Task.objects.filter(pk=personal.pk).exists())  # personal survives

    def test_member_cannot_delete_workspace(self):
        self.client.login(username='bob', password='pw12345!')
        response = self.client.post(self._url('workspace_delete'))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())

    def test_delete_404_for_non_member(self):
        self.client.login(username='carol', password='pw12345!')
        response = self.client.post(self._url('workspace_delete'))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Workspace.objects.filter(pk=self.ws.pk).exists())
