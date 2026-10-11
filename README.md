# Task Scheduling System Using a Greedy Algorithm

A personal task scheduler built with Django, with optional team
workspaces for supervised collaboration. Tasks are automatically
ordered by a hybrid greedy algorithm that combines user-assigned
priority with deadline urgency.

## Scheduling algorithm

```
P = priority, raw value 1-10 (10 = highest, never normalized)
D = FRACTIONAL days remaining before the deadline (clamped at 0 if
    overdue) — deadlines carry a time of day, so D is a float
U = 1 / (D + 1)
S = 0.7 * P + 0.3 * U
```

Tasks are sorted by descending `S`. Ties are broken by (1) earlier
deadline, then (2) shorter duration, compared in normalized minutes so
"1 hour" always sorts before "1 week" regardless of the stored unit.
Scoring is O(n); sorting is O(n log n); overall complexity is
O(n log n). See `scheduler/algorithms/greedy_scheduler.py`.

## Features

- Account registration (email required) and login (Django's built-in
  auth, hashed passwords)
- Task CRUD, scoped to the logged-in user; deadlines include a time of
  day, durations are a value + unit pair (minutes/hours/days/weeks/
  months) normalized to minutes internally
- Progress tracking: per-task status (not started / in progress /
  completed) with a colored badge and a 0-100% progress bar; completed
  tasks are excluded from generated schedules, overload checks and
  reminders
- Workspaces: create a workspace, invite members by username or email
  with a supervisor/member role, and assign tasks to them
- Workspace management: rename a workspace, promote/demote members,
  remove a member (their workspace tasks are handed to the supervisor),
  leave a workspace (last supervisor blocked), and delete a workspace
  behind a confirmation page — every rule enforced server-side
- Team Overview: supervisors see every member's WORKSPACE tasks side by
  side — personal tasks (workspace=None) are private to their owner and
  never appear in any supervisor-facing view or query
- Each person's schedule is computed from their own tasks alone
  (personal + assigned to them) — schedules are never merged across
  users
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
demo), create a Gmail
[app password](https://myaccount.google.com/apppasswords) (requires
2-Step Verification; regular passwords are rejected) and put it in a
`.env` file at the project root:

```bash
DJANGO_EMAIL_HOST_USER=youraddress@gmail.com
DJANGO_EMAIL_HOST_PASSWORD=your16characterapppassword
```

`settings.py` loads this file automatically for **every** process —
`runserver`, `manage.py send_reminders` from cron/Task Scheduler, even
tests — and as soon as `DJANGO_EMAIL_HOST_PASSWORD` is set the backend
switches from console to SMTP on its own (no other variable needed).
`.env` is in `.gitignore`, so the secret is never committed.

> **Security:** `.env` holds the app password in plain text, so it
> must never be committed, shared, or pasted into a screenshot. If
> this machine is ever shared or sold, revoke the app password at
> [apppasswords](https://myaccount.google.com/apppasswords) and create
> a new one — deleting the file alone is not enough.

Environment variables work too and take precedence over `.env` — set
them before running the server, e.g. on Linux/macOS:

```bash
export DJANGO_EMAIL_HOST_USER=youraddress@gmail.com
export DJANGO_EMAIL_HOST_PASSWORD=your16characterapppassword
```

(On Windows, use `set` instead of `export`, or add these to PyCharm's
Run Configuration environment variables. `DJANGO_EMAIL_BACKEND` still
overrides the automatic backend choice if you set it.)

**Keeping the password off disk entirely (optional):** because real
environment variables win over `.env`, you can leave
`DJANGO_EMAIL_HOST_PASSWORD` *empty* in `.env` and set it only in the
current shell session whenever you need real sending:

```bash
# Linux/macOS — lives only in this terminal window, gone on exit
export DJANGO_EMAIL_HOST_PASSWORD="your16characterapppassword"
python manage.py send_reminders
unset DJANGO_EMAIL_HOST_PASSWORD   # optional: drop it right away
```

```bat
:: Windows cmd — same idea for one window
set DJANGO_EMAIL_HOST_PASSWORD=your16characterapppassword
python manage.py send_reminders
set DJANGO_EMAIL_HOST_PASSWORD=
```

The trade-off: each new shell must set it again, and a scheduled
`send_reminders` (cron / Task Scheduler) has no interactive shell, so
the scheduled job either needs the variable exported in its
environment or the password left in `.env` — that is why `.env`
remains the default flow.

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
  - **Windows (Task Scheduler):** no cron on Windows — see
    [Windows Task Scheduler setup (tested)](#windows-task-scheduler-setup-tested)
    below for the exact, live-tested commands (wrapper script,
    `Register-ScheduledTask` line, and the one-line sanity check that
    proves it fired).

### Windows Task Scheduler setup (tested)

Windows has no cron, so Task Scheduler calls
`send_reminders` instead. The project ships **`run_reminders.bat`**
at the root for exactly this job. It:

- `cd`s into its own folder (`cd /d "%~dp0"`), so Task Scheduler's
  working directory — which is easy to get wrong — can't matter;
- runs the **venv's** `python.exe`, so no PATH or activation setup is
  needed;
- appends a timestamped `run_reminders START` … `run_reminders END
  exit=<code>` pair to **`reminders.log`** around every run (already
  covered by `*.log` in `.gitignore`), which is your proof of firing.

**1. Create the task.** Open PowerShell and run — this exact command
was tested end-to-end on this machine (change the path if the folder
moves):

```powershell
$bat = 'C:\Users\Admin\Desktop\new pproject\Taskscheduler\run_reminders.bat'
Register-ScheduledTask -TaskName 'TaskScheduler-Reminders' `
  -Action (New-ScheduledTaskAction -Execute $bat -WorkingDirectory (Split-Path $bat)) `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00AM) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable) `
  -Description 'FR7 background reminder emails - see README'
```

What each piece is:

- **Command line:** the full path to `run_reminders.bat` — nothing
  else; the wrapper picks the interpreter itself.
- **Working directory:** the project folder
  (`Split-Path $bat`) — belt-and-braces on top of the `cd` inside the
  wrapper.
- **Trigger:** daily at 07:00 — the Windows equivalent of the cron
  line above (use `-Weekly`/`-At` values for other schedules).
- **The battery flags are not decoration.** Task Scheduler's default
  — used by both the "Basic Task" wizard and `schtasks /create` — is
  *"don't start while on battery power"*. Tested live on this
  machine while unplugged: a task created with those defaults sat at
  `Status: Queued` and never started, while the line above ran
  immediately. `-DontStopIfGoingOnBatteries` also keeps a brief
  power-source change from killing a run mid-send, and
  `-StartWhenAvailable` runs a missed 7:00 (laptop asleep) as soon as
  the machine wakes. Do not skip them, and prefer this
  `Register-ScheduledTask` line over the GUI wizard for that reason.

**2. Force-run it once now** — don't wait until tomorrow to find out
it doesn't work:

```powershell
Start-ScheduledTask -TaskName 'TaskScheduler-Reminders'
```

**3. Sanity check — one line:**

```powershell
Get-Content reminders.log -Tail 3
```

You should see a line stamped with *today's* time, e.g.
`[Fri 10/09/2026 9:54:30.53] run_reminders END exit=0`, with the
`Sent reminder to …` / `Done.` output above it. A fresh `END exit=0`
is the proof the task actually fired and the send succeeded;
`schtasks /query /tn "TaskScheduler-Reminders"` likewise shows
`Last Result: 0`. Run the same one-liner after any future scheduled
run — it is the permanent "did it fire?" check.

To stop and remove the task:
`Unregister-ScheduledTask -TaskName 'TaskScheduler-Reminders' -Confirm:$false`.

## Project structure

```
task_scheduler/        Django project settings, root URLs
run_reminders.bat      Windows Task Scheduler wrapper (see Email reminders)
scheduler/
    models.py           Task, Workspace, WorkspaceMembership models
    forms.py             TaskForm (incl. assignment), WorkspaceForm, InviteMemberForm, RegisterForm
    permissions.py       Server-side workspace/task authorization rules
    views.py             Auth, dashboard, task CRUD, schedule, PDF, workspace/supervision views
    urls.py               App URL routes
    algorithms/
        greedy_scheduler.py   The scoring/sorting algorithm (single source of truth)
    services/
        pdf_service.py        PDF export, built from the same scheduled data as the page
        reminder_service.py   Due-soon/overdue tracking, email reminders (FR7), day-overload check
    management/commands/
        send_reminders.py     Background process behind FR7 — run on a schedule (see above)
    templates/            Auth pages + scheduler/workspace pages, all extending base.html
    static/scheduler/     CSS and JS
    tests.py              Auth, CRUD, isolation, algorithm, time spans, progress,
                          workspaces, supervision/privacy, email send & retry,
                          SQLite→PostgreSQL dump/load drill
```

## Before deployment

- Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, and `DJANGO_ALLOWED_HOSTS`
  as environment variables.
- Move data from SQLite to PostgreSQL — see
  [Migrating from SQLite to PostgreSQL](#migrating-from-sqlite-to-postgresql)
  below. The backend is switched with environment variables, so
  `settings.py` needs no edit and SQLite stays the default.
- Run `python manage.py collectstatic`.

## Migrating from SQLite to PostgreSQL

SQLite remains the default for development — nothing here changes
that. This is the tested path for moving existing data into a fresh
PostgreSQL database when deployment is actually being prepared. The
exact commands below were drilled end-to-end against a real
PostgreSQL 16 server (an isolated instance on port 5433): 2 users,
1 task, 1 workspace and 1 membership round-tripped, password hashes
still verifiable and foreign keys resolved.

**1. Install the driver.** Uncomment `psycopg2-binary>=2.9` in
`requirements.txt`, then:

```bash
pip install -r requirements.txt
```

**2. Tell Django to use PostgreSQL** (environment variables only —
`settings.py` reads them and falls back to SQLite when
`DJANGO_DB_ENGINE` is unset, so there is no code edit and no risk of
committing a backend change):

```bat
:: Windows cmd — one-off, this window only
set DJANGO_DB_ENGINE=django.db.backends.postgresql
set DJANGO_DB_NAME=taskscheduler
set DJANGO_DB_USER=postgres
set DJANGO_DB_PASSWORD=yourpgpassword
set DJANGO_DB_HOST=127.0.0.1
set DJANGO_DB_PORT=5432
```

```bash
# Linux/macOS — same variables with export
export DJANGO_DB_ENGINE=django.db.backends.postgresql
export DJANGO_DB_NAME=taskscheduler
export DJANGO_DB_USER=postgres
export DJANGO_DB_PASSWORD=yourpgpassword
export DJANGO_DB_HOST=127.0.0.1
export DJANGO_DB_PORT=5432
```

Put them in `.env` (git-ignored, loaded automatically by
`settings.py`) if the scheduled `send_reminders` job must see them
too. Leave them **unset** everywhere else and the app keeps using
`db.sqlite3` exactly as before.

**3. Create the empty database and its tables** (with step 2 set):

```bash
createdb -U postgres taskscheduler   # or: psql -U postgres -c "CREATE DATABASE taskscheduler;"
python manage.py migrate
```

**4. Dump from SQLite, then load into PostgreSQL.** The dump must be
taken while Django still points at SQLite, and loaded while it points
at PostgreSQL — so run the two commands in two different shells (or
`set`/`unset` the `DJANGO_DB_ENGINE` variable between them):

```bash
# Shell A — engine UNSET (SQLite), from the project folder:
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --exclude sessions --output db_dump.json

# Shell B — DJANGO_DB_ENGINE=... set (step 2), same folder:
python manage.py loaddata db_dump.json
```

The three `--exclude` flags are not optional — see the troubleshooting
note below.

**5. Verify** (in shell B):

```bash
python manage.py shell -c "from django.contrib.auth.models import User; from scheduler.models import Task, Workspace; print('users', User.objects.count(), 'tasks', Task.objects.count(), 'workspaces', Workspace.objects.count())"
```

Compare against `python manage.py shell -c "..."` on the SQLite side
(same command with the variables unset), then log in through the UI
and check your tasks are there before deleting anything.

### Troubleshooting: the duplicate-key pitfall

The classic failure with this stack is dumping **with** content types
and permissions and loading that into a database `migrate` has
already set up. Reproduced here with the real error:

```
IntegrityError: Problem installing fixture 'db_dump.json': Could not load
contenttypes.ContentType(pk=8): duplicate key value violates unique
constraint "django_content_type_app_label_model_76bd3d3b_uniq"
DETAIL: Key (app_label, model)=(scheduler, workspacemembership) already exists.
```

**Why:** step 3's `migrate` already created every `ContentType` and
`Permission` row from the migrations; a full dump tries to insert
them a second time, and the unique `(app_label, model)` constraint
rejects it. **Fix:** always pass the three `--exclude` flags exactly
as in step 4 — the target database regenerates content types and
permissions itself during `migrate`, and sessions are runtime state
you don't want to move.

Two related notes:

- **Never load a dump into a database that already contains that
data** — even a correct dump will hit duplicate primary keys on the
second load. Load into a freshly migrated database (step 3), or clear
the tables first.
- **Foreign keys and sequences need no manual fixes on Django 4.2:**
  `loaddata` disables constraint checks while loading, re-checks them
  afterwards, and resets PostgreSQL sequences (`setval`) itself — so
  row order in the JSON doesn't matter and the next created row gets
  a fresh id with no collision (verified in the drill). If a load
  fails with an FK or sequence error anyway, the dump is stale or
  partial: redo step 4 from scratch rather than hand-editing the JSON.

## Note on this build

The full suite passes locally: `python manage.py test` → 126 tests OK,
and `python manage.py makemigrations scheduler --check` reports no
drift between `models.py` and the included migrations (0001–0005). The code follows
Django conventions throughout and every migration was generated (or
data-backed, where a field was replaced) to match `models.py` exactly — run
`python manage.py makemigrations scheduler --check` locally first to
confirm nothing is out of sync before you build on top of it.
