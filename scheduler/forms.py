from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Task


class TaskForm(forms.ModelForm):
    """Create/edit form for a Task.

    Validation beyond the model's own validators (priority 1-10, duration
    a positive integer) is kept deliberately small — this is a personal
    scheduler, not a general-purpose project-management tool.
    """

    deadline = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    class Meta:
        model = Task
        fields = ['title', 'description', 'deadline', 'priority', 'duration']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'e.g. Submit Chapter 3 draft'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional details'}),
            'priority': forms.NumberInput(attrs={'min': 1, 'max': 10}),
            'duration': forms.NumberInput(attrs={'min': 1, 'placeholder': 'Minutes'}),
        }

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if not title:
            raise forms.ValidationError('Title cannot be empty.')
        return title

    def clean_duration(self):
        duration = self.cleaned_data['duration']
        if duration <= 0:
            raise forms.ValidationError('Duration must be a positive number of minutes.')
        return duration


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
