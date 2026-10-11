"""
Django settings for the Task Scheduling System Using a Greedy Algorithm.

Keep this file simple and defensible: a single-user-per-account personal
task scheduler, not a multi-tenant commercial product.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Optional .env file at the project root — loads DJANGO_* variables into
# os.environ so SMTP credentials (and friends) apply to *every* process:
# runserver, `manage.py send_reminders` from cron/Task Scheduler, tests.
# No third-party dependency; values already present in the real
# environment always win (setdefault). .env is listed in .gitignore, so
# secrets are never committed.
_env_file = BASE_DIR / '.env'
if _env_file.is_file():
    for _line in _env_file.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if not _line or _line.startswith('#') or '=' not in _line:
            continue
        _key, _, _value = _line.partition('=')
        os.environ.setdefault(_key.strip(), _value.strip().strip('"\''))

# SECURITY WARNING: keep the secret key used in production secret!
# Reads from the environment so it is never committed to source control.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'dev-only-insecure-key-change-before-deployment',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'scheduler',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'task_scheduler.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'scheduler' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'task_scheduler.wsgi.application'
ASGI_APPLICATION = 'task_scheduler.asgi.application'

# Database
# SQLite during development. Switch to PostgreSQL before deployment by
# setting the DATABASE_URL-style environment variables below and swapping
# the ENGINE/NAME/USER/PASSWORD/HOST/PORT keys — do not do this until
# deployment is actually being prepared.
# SQLite stays the default for development. Setting
# DJANGO_DB_ENGINE=django.db.backends.postgresql (plus the DJANGO_DB_*
# variables below, e.g. in .env) switches to PostgreSQL with no code
# change — see README.md, "Migrating from SQLite to PostgreSQL", for
# the tested dump/load drill. DJANGO_DB_NAME is the SQLite file
# (default db.sqlite3, always anchored at BASE_DIR so any working
# directory works) or the PostgreSQL database name (default
# taskscheduler), depending on the engine.
DB_ENGINE = os.environ.get('DJANGO_DB_ENGINE', 'django.db.backends.sqlite3')

DATABASES = {
    'default': {
        'ENGINE': DB_ENGINE,
        'NAME': (
            BASE_DIR / os.environ.get('DJANGO_DB_NAME', 'db.sqlite3')
            if 'sqlite' in DB_ENGINE
            else os.environ.get('DJANGO_DB_NAME', 'taskscheduler')
        ),
        'USER': os.environ.get('DJANGO_DB_USER', ''),
        'PASSWORD': os.environ.get('DJANGO_DB_PASSWORD', ''),
        'HOST': os.environ.get('DJANGO_DB_HOST', ''),
        'PORT': os.environ.get('DJANGO_DB_PORT', ''),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Lagos'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'scheduler' / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Auth flow — see scheduler/urls.py for the matching named URLs.
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# Email (FR7 — deadline reminders).
#
# !! SECURITY WARNING — READ BEFORE TOUCHING THE VALUES BELOW !!
# The Gmail app password backing EMAIL_HOST_PASSWORD is a real secret
# and is stored (when set) as PLAIN TEXT in BASE_DIR/.env. That file
# MUST NEVER be committed to git or shared: it is listed in
# .gitignore — keep it that way — and must not end up in screenshots,
# emails, or public folders. Deleting it is not enough if a copy or
# git history survives. If this laptop is ever shared, lent, or sold,
# ROTATE the app password at https://myaccount.google.com/apppasswords
# immediately. To keep the secret off disk entirely, set
# DJANGO_EMAIL_HOST_PASSWORD as an environment variable in the current
# shell session instead — real environment variables take precedence
# over .env (see README.md, "Email reminders (FR7)").
#
# Defaults to Django's console backend, which just prints the email to
# the terminal running runserver — so reminders work out of the box
# with zero setup, including during a panel demo with no real mailbox
# involved. Once .env (or the real environment) provides
# DJANGO_EMAIL_HOST_PASSWORD, the backend switches to SMTP
# automatically and real mail leaves the machine;
# DJANGO_EMAIL_BACKEND still overrides everything if set (see README.md
# for how to create the Gmail app password). No third-party email API
# is used anywhere.
EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend'
    if os.environ.get('DJANGO_EMAIL_HOST_PASSWORD')
    else 'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.environ.get('DJANGO_EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('DJANGO_EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('DJANGO_EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('DJANGO_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get(
    'DJANGO_DEFAULT_FROM_EMAIL', 'Task Scheduler <noreply@taskscheduler.local>'
)

MESSAGE_TAGS = {
    10: 'debug',
    20: 'info',
    25: 'success',
    30: 'warning',
    40: 'error',
}
