from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Task(models.Model):
    """A single personal task belonging to one user.

    Ownership is enforced everywhere a Task is read or written: every
    queryset in views.py is filtered by `user=request.user`, so one
    account can never see or touch another account's tasks.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    deadline = models.DateField(
        help_text='The date this task is due.'
    )
    priority = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text='1 (lowest) to 10 (highest).',
    )
    duration = models.PositiveIntegerField(
        help_text='Estimated duration in minutes.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['deadline']

    def __str__(self):
        return self.title
