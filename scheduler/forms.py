from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import DURATION_UNIT_CHOICES, Task, Workspace, WorkspaceMembership


class TaskForm(forms.ModelForm):
    """Create/edit form for a Task.

    Deadline takes a date *and* a time (datetime-local input, with
    date-only values accepted for convenience and treated as midnight).
    Duration is a value + unit pair, normalized to minutes by
    Task.duration_minutes wherever minutes are needed.

    Validation beyond the model's own validators (priority 1-10,
    percent 0-100, positive duration) is kept deliberately small.

    `assignable_users` is the server-side reassignment gate: when the
    caller (a workspace supervisor) passes a user queryset, an
    "assigned_to" picker is shown and validated against it; when it is
    omitted — every personal-task flow — the field is removed from the
    form entirely, so a non-supervisor can never reassign a task no
    matter what they POST.
    """

    deadline = forms.DateTimeField(
        input_formats=[
            '%Y-%m-%dT%H:%M',             # datetime-local browser input
            '%Y-%m-%dT%H:%M:%S',          # isoformat-style, seconds
            '%Y-%m-%dT%H:%M:%S.%f',       # isoformat-style, microseconds
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%d',                   # date-only: midnight of that day
        ],
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M',
        ),
    )

    # Declared as optional form fields backed by the model defaults:
    # an empty status falls back to "not_started", an empty percent to 0.
    status = forms.ChoiceField(
        choices=Task.STATUS_CHOICES, required=False, initial=Task.STATUS_NOT_STARTED,
    )
    percent_complete = forms.IntegerField(
        min_value=0, max_value=100, required=False, initial=0,
    )

    class Meta:
        model = Task
        fields = [
            'title', 'description', 'deadline', 'priority',
            'duration_value', 'duration_unit', 'status', 'percent_complete',
            'assigned_to',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'e.g. Submit Chapter 3 draft'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional details'}),
            'priority': forms.NumberInput(attrs={'min': 1, 'max': 10}),
            'duration_value': forms.NumberInput(attrs={'min': 1, 'placeholder': 'e.g. 90'}),
        }

    def __init__(self, *args, assignable_users=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The live badge next to the field advertises 5 as the default,
        # so the input must actually start at 5 (the model itself keeps
        # priority strictly required, 1-10, with no silent fallback).
        self.fields['priority'].initial = 5
        if assignable_users is None:
            # No supervisor context -> reassignment is impossible, not
            # merely hidden: the field never enters cleaned_data, so
            # construct_instance leaves the task's assignee untouched.
            self.fields.pop('assigned_to', None)
        else:
            self.fields['assigned_to'].queryset = assignable_users
            self.fields['assigned_to'].label = 'Assign to'

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if not title:
            raise forms.ValidationError('Title cannot be empty.')
        return title

    def clean_duration_value(self):
        value = self.cleaned_data['duration_value']
        if value <= 0:
            raise forms.ValidationError('Duration must be a positive number.')
        return value

    def clean_deadline(self):
        deadline = self.cleaned_data['deadline']
        if timezone.is_naive(deadline):
            deadline = timezone.make_aware(deadline)
        return deadline

    def clean_status(self):
        return self.cleaned_data.get('status') or Task.STATUS_NOT_STARTED

    def clean_percent_complete(self):
        percent = self.cleaned_data.get('percent_complete')
        return 0 if percent is None else percent


class WorkspaceForm(forms.ModelForm):
    """Create a workspace (the creator becomes its supervisor)."""

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Final Year Project'}),
    )

    class Meta:
        model = Workspace
        fields = ['name']


class InviteMemberForm(forms.Form):
    """Add an existing account to a workspace, by username or email."""

    identifier = forms.CharField(
        label='Username or email',
        max_length=254,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. alice or alice@example.com'}),
    )
    role = forms.ChoiceField(choices=WorkspaceMembership.ROLE_CHOICES)


class RegisterForm(UserCreationForm):
    """Account creation form built on Django's built-in password handling.

    Email is required (not Django's default) because FR7 — email
    deadline reminders — depends on every account having one.
    """

    email = forms.EmailField(required=True)
    field_order = ['username', 'email', 'password1', 'password2']

    class Meta:
        model = User
        fields = ['username', 'email']
