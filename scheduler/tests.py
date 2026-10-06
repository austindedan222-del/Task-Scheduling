from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .algorithms.greedy_scheduler import compute_score, generate_schedule
from .models import Task
from .services.reminder_service import (
    DAY_CAPACITY_MINUTES,
    get_overloaded_days,
    send_reminder_email,
)


def make_task(user, title='Task', days_ahead=5, priority=5, duration=30):
    return Task.objects.create(
        user=user,
        title=title,
        deadline=date.today() + timedelta(days=days_ahead),
        priority=priority,
        duration=duration,
    )


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
            'duration': 90,
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
            'duration': task.duration,
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
            'duration': 30,
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
            'duration': 20,
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
            'duration': 10,
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
        short = Task.objects.create(user=user, title='Short', deadline=same_day, priority=5, duration=15)
        long = Task.objects.create(user=user, title='Long', deadline=same_day, priority=5, duration=120)
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
        task = Task(user=user, title='Bad', deadline=date.today(), priority=11, duration=10)
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
        Task.objects.create(user=user, title='A', deadline=same_day, priority=5, duration=1000)
        Task.objects.create(user=user, title='B', deadline=same_day, priority=5, duration=1000)
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(len(overloaded), 1)
        self.assertEqual(overloaded[0]['date'], same_day)
        self.assertEqual(overloaded[0]['total_minutes'], 2000)
        self.assertGreater(overloaded[0]['total_minutes'], DAY_CAPACITY_MINUTES)

    def test_exactly_capacity_is_not_flagged(self):
        # 1440 minutes is a full day, not *more* than a day — boundary check.
        user = User.objects.create_user('jade', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(user=user, title='Full day', deadline=same_day, priority=5, duration=DAY_CAPACITY_MINUTES)
        overloaded = get_overloaded_days(Task.objects.filter(user=user))
        self.assertEqual(overloaded, [])

    def test_overload_warning_shown_on_dashboard(self):
        user = User.objects.create_user('kay', password='pw12345!')
        self.client.login(username='kay', password='pw12345!')
        same_day = date.today() + timedelta(days=5)
        Task.objects.create(user=user, title='A', deadline=same_day, priority=5, duration=1000)
        Task.objects.create(user=user, title='B', deadline=same_day, priority=5, duration=1000)
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
