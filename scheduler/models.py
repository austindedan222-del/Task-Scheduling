from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


# Duration units for Task.duration_unit, normalized to minutes wherever a
# raw minute count is needed (overload/capacity checks, the schedule's
# duration tie-break). One month is taken as 30 days.
DURATION_UNIT_MINUTES = {
    'minutes': 1,
    'hours': 60,
    'days': 1440,
    'weeks': 7 * 1440,
    'months': 30 * 1440,
}

DURATION_UNIT_CHOICES = [
    ('minutes', 'Minutes'),
    ('hours', 'Hours'),
    ('days', 'Days'),
    ('weeks', 'Weeks'),
    ('months', 'Months'),
]


class Workspace(models.Model):
    """A shared space a supervisor and their members collaborate in.

    Workspaces only ever hold tasks that were explicitly assigned
    inside them. A task with workspace=None is personal and is never
    visible to any supervisor (see scheduler/permissions.py).
    """

    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspaces_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class WorkspaceMembership(models.Model):
    """One user's role in one workspace (unique per pair).

    A user can hold a different role in each workspace they belong to:
    supervisor in one, member in another.
    """

    ROLE_SUPERVISOR = 'supervisor'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_SUPERVISOR, 'Supervisor'),
        (ROLE_MEMBER, 'Member'),
    ]

    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspace_memberships',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['joined_at']
        unique_together = ('workspace', 'user')

    def __str__(self):
        return f'{self.user.username} in {self.workspace.name} ({self.role})'


class Task(models.Model):
    """A single task belonging to one user.

    Ownership is enforced everywhere a Task is read or written: every
    queryset in views.py is filtered by `assigned_to=request.user`, so one
    account can never see or touch another account's tasks unless a
    workspace supervisor rule explicitly allows it (see
    scheduler/permissions.py).
    """

    STATUS_NOT_STARTED = 'not_started'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, 'Not Started'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    workspace = models.ForeignKey(
        Workspace,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='tasks',
        help_text='Null = personal task, private to its owner. Only tasks '
                  'inside a workspace are visible to that workspace\'s '
                  'supervisor.',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assigned_tasks',
        help_text='Whose task it is: the creator for personal tasks, the '
                  'chosen member for workspace tasks.',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks_assigned',
        help_text='Who assigned it; null for personal tasks.',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    deadline = models.DateTimeField(
        help_text='The date and time this task is due.'
    )
    priority = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text='1 (lowest) to 10 (highest).',
    )
    duration_value = models.PositiveIntegerField(
        help_text='Estimated duration, as a number of the unit below.'
    )
    duration_unit = models.CharField(
        max_length=10,
        choices=DURATION_UNIT_CHOICES,
        default='minutes',
        help_text='Unit for the duration value.',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NOT_STARTED,
        help_text='Progress status of the task.',
    )
    percent_complete = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='0-100; only meaningful while the task is in progress.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['deadline']

    def __str__(self):
        return self.title

    @property
    def duration_minutes(self):
        """The duration normalized to whole minutes, regardless of unit.

        This is the single conversion used everywhere minutes matter:
        the overload/capacity check and the schedule's "shorter duration"
        tie-break.
        """
        return self.duration_value * DURATION_UNIT_MINUTES[self.duration_unit]
