# Task Scheduling System Using a Greedy Algorithm

A personal, single-user task scheduler built with Django. Tasks are
automatically ordered by a hybrid greedy algorithm that combines
user-assigned priority with deadline urgency.

## Scheduling algorithm

```
P = priority, raw value 1-10 (10 = highest, never normalized)
D = whole days remaining before the deadline (clamped at 0 if overdue)
U = 1 / (D + 1)
S = 0.7 * P + 0.3 * U
```

Tasks are sorted by descending `S`. Ties are broken by (1) earlier
deadline, then (2) shorter duration. Scoring is O(n); sorting is
O(n log n); overall complexity is O(n log n). See
`scheduler/algorithms/greedy_scheduler.py`.

## Features

- Account registration (email required) and login (Django's built-in
  auth, hashed passwords)
- Task CRUD, scoped to the logged-in user
- Automatic schedule generation
- PDF export of the generated schedule
- In-app "due soon" / "overdue" reminders on the dashboard
- Email deadline reminders (FR7) — a `send_reminders` management command
  for scheduled/background sending, plus an on-demand "Email My
  Reminders Now" button on the dashboard for live demos. Uses Django's
  own mail framework — no third-party notification API
- Day-overload warning (Reliability NFR) — flags any date where the
  combined duration of tasks due that day exceeds 24 hours
- Responsive, authentication-aware UI

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python download_assets.py        # one-time: pulls Bootstrap + icon font into
                                  # static/ so the app runs with no CDN/internet

python manage.py makemigrations  # confirms the included migration matches models.py
python manage.py migrate
python manage.py createsuperuser # optional, for /admin/
python manage.py test            # run the test suite
python manage.py runserver
```

`download_assets.py` only needs to be run once — after that, Bootstrap and
the icon font load from your own `static/` folder, not a CDN, so the app
loads instantly even with no internet (useful for a panel demo). Re-run it
any time if you ever delete the `scheduler/static/scheduler/vendor/` folder.

Then visit http://127.0.0.1:8000/ — you'll land on the login page.
New users can click "Create Account" to register.

## Email reminders (FR7)

By default, `EMAIL_BACKEND` is Django's console backend: reminder
emails aren't sent anywhere, they just print to the terminal running
`runserver`. This means reminders work immediately, with zero setup,
including during a panel demo — click "Email My Reminders Now" on the
dashboard and watch the email appear in the terminal.

To send real email (e.g. to actually receive it in an inbox during a
demo), set these environment variables before running the server —
a Gmail account with an
[app password](https://myaccount.google.com/apppasswords) works well:

```bash
export DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
export DJANGO_EMAIL_HOST_USER=youraddress@gmail.com
export DJANGO_EMAIL_HOST_PASSWORD=your16characterapppassword
```

(On Windows, use `set` instead of `export`, or add these to PyCharm's
Run Configuration environment variables.)

There are two ways reminders get sent:

- **On demand** — the "Email My Reminders Now" button on the dashboard,
  for the signed-in user only (good for a live demo).
- **In the background** — `python manage.py send_reminders` checks
  every user's tasks and emails anyone with something due soon or
  overdue. This is the actual FR7 "background" behaviour; Django has no
  built-in scheduler, so the normal way to run this periodically is to
  let the operating system call it on a timer:
  - **Linux/macOS (cron):** `crontab -e`, then add a line like
    `0 7 * * * cd /path/to/Taskscheduler && /path/to/.venv/bin/python manage.py send_reminders`
    to run it every morning at 7am.
  - **Windows (Task Scheduler):** create a new Basic Task that runs
    `python.exe` with argument `manage.py send_reminders` and "Start in"
    set to the project folder, on whatever schedule you like.

## Project structure

```
task_scheduler/        Django project settings, root URLs
scheduler/
    models.py           Task model
    forms.py             TaskForm, RegisterForm
    views.py             Auth, dashboard, task CRUD, schedule, PDF views
    urls.py               App URL routes
    algorithms/
        greedy_scheduler.py   The scoring/sorting algorithm (single source of truth)
    services/
        pdf_service.py        PDF export, built from the same scheduled data as the page
        reminder_service.py   Due-soon/overdue tracking, email reminders (FR7), day-overload check
    management/commands/
        send_reminders.py     Background process behind FR7 — run on a schedule (see above)
    templates/            Auth pages + scheduler pages, all extending base.html
    static/scheduler/     CSS and JS
    tests.py              Auth, CRUD, ownership isolation, algorithm, PDF, email & overload tests
```

## Before deployment

- Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, and `DJANGO_ALLOWED_HOSTS`
  as environment variables.
- Switch `DATABASES['default']` in `task_scheduler/settings.py` to
  PostgreSQL and uncomment `psycopg2-binary` in `requirements.txt`.
- Run `python manage.py collectstatic`.

## Note on this build

This repository was generated without network access, so the Django
package itself was not available to actually run `makemigrations`,
`migrate`, or the test suite in this environment. The code follows
Django conventions throughout and the included migration was
hand-written to match `models.py` exactly — run
`python manage.py makemigrations --check` locally first to confirm
nothing is out of sync before you build on top of it.
