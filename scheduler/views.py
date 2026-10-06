from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .algorithms.greedy_scheduler import generate_schedule
from .forms import RegisterForm, TaskForm
from .models import Task
from .services.pdf_service import render_schedule_pdf
from .services.reminder_service import (
    get_due_soon_tasks,
    get_overdue_tasks,
    get_overloaded_days,
    send_reminder_email,
)


def is_ajax(request):
    """True for fetch() requests sent by main.js's modal/table JS.

    Every AJAX-capable view here still works perfectly without this —
    a non-JS browser just gets the normal full-page render/redirect.
    """
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _render_task_table(request):
    tasks = Task.objects.filter(user=request.user)
    return render_to_string('scheduler/partials/task_table.html', {'tasks': tasks}, request=request)


class RegisterView(CreateView):
    """Account creation. Django's UserCreationForm handles password
    hashing and confirmation; we never touch or store a raw password.
    """
    form_class = RegisterForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('login')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            'Account created successfully. You can now log in.',
        )
        return response


@login_required
def dashboard(request):
    tasks = Task.objects.filter(user=request.user)
    context = {
        'task_count': tasks.count(),
        'due_soon': get_due_soon_tasks(tasks),
        'overdue': get_overdue_tasks(tasks),
        'overloaded_days': get_overloaded_days(tasks),
        'has_tasks': tasks.exists(),
    }
    return render(request, 'scheduler/dashboard.html', context)


@login_required
def send_reminder_now(request):
    """Lets a user trigger their own reminder email on demand — handy for
    a live demo, since the real "background" dispatch (FR7) is the
    send_reminders management command meant to run on a schedule (see
    README.md), not something a browser click normally triggers.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    tasks = Task.objects.filter(user=request.user)
    due_soon = get_due_soon_tasks(tasks)
    overdue = get_overdue_tasks(tasks)

    if not request.user.email:
        messages.error(request, 'Add an email address to your account to receive reminders.')
    elif send_reminder_email(request.user, due_soon, overdue):
        messages.success(request, f'Reminder email sent to {request.user.email}.')
    else:
        messages.info(request, 'Nothing due soon or overdue — no reminder needed right now.')

    return redirect('dashboard')


@login_required
def task_list(request):
    tasks = Task.objects.filter(user=request.user)
    return render(request, 'scheduler/task_list.html', {'tasks': tasks})


@login_required
def task_create(request):
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.save()
            if is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'message': f'"{task.title}" was added.',
                    'table_html': _render_task_table(request),
                })
            messages.success(request, f'"{task.title}" was added.')
            return redirect('task_list')

        if is_ajax(request):
            html = render_to_string(
                'scheduler/partials/task_form_fields.html',
                {'form': form, 'mode': 'create'},
                request=request,
            )
            return JsonResponse({'success': False, 'html': html}, status=400)
    else:
        form = TaskForm()

    if is_ajax(request):
        html = render_to_string(
            'scheduler/partials/task_form_fields.html',
            {'form': form, 'mode': 'create'},
            request=request,
        )
        return JsonResponse({'html': html, 'title': 'Add Task'})

    return render(request, 'scheduler/task_form.html', {'form': form, 'mode': 'create'})


@login_required
def task_update(request, pk):
    # Scoped by both pk AND user=request.user — a user can never edit
    # another account's task by guessing/changing the URL id.
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            if is_ajax(request):
                return JsonResponse({
                    'success': True,
                    'message': f'"{task.title}" was updated.',
                    'table_html': _render_task_table(request),
                })
            messages.success(request, f'"{task.title}" was updated.')
            return redirect('task_list')

        if is_ajax(request):
            html = render_to_string(
                'scheduler/partials/task_form_fields.html',
                {'form': form, 'mode': 'edit', 'task': task},
                request=request,
            )
            return JsonResponse({'success': False, 'html': html}, status=400)
    else:
        form = TaskForm(instance=task)

    if is_ajax(request):
        html = render_to_string(
            'scheduler/partials/task_form_fields.html',
            {'form': form, 'mode': 'edit', 'task': task},
            request=request,
        )
        return JsonResponse({'html': html, 'title': f'Edit "{task.title}"'})

    return render(request, 'scheduler/task_form.html', {'form': form, 'mode': 'edit', 'task': task})


@login_required
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if request.method == 'POST':
        title = task.title
        task.delete()
        if is_ajax(request):
            return JsonResponse({
                'success': True,
                'message': f'"{title}" was deleted.',
                'table_html': _render_task_table(request),
            })
        messages.success(request, f'"{title}" was deleted.')
        return redirect('task_list')

    if is_ajax(request):
        html = render_to_string(
            'scheduler/partials/task_delete_fields.html', {'task': task}, request=request,
        )
        return JsonResponse({'html': html, 'title': f'Delete "{task.title}"?'})

    return render(request, 'scheduler/task_confirm_delete.html', {'task': task})


@login_required
def schedule_view(request):
    tasks = Task.objects.filter(user=request.user)
    scheduled = generate_schedule(tasks)
    context = {
        'scheduled': scheduled,
        'overloaded_days': get_overloaded_days(tasks),
    }
    return render(request, 'scheduler/schedule.html', context)


@login_required
def schedule_pdf(request):
    tasks = Task.objects.filter(user=request.user)
    scheduled = generate_schedule(tasks)
    return render_schedule_pdf(request.user.username, scheduled)


def help_page(request):
    """Static how-to guide for the whole system. Public (like the login
    and register pages) so people can read it before creating an account.
    """
    return render(request, 'scheduler/help.html')
