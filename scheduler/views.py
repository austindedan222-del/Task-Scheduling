from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .algorithms.greedy_scheduler import generate_schedule
from .forms import InviteMemberForm, RegisterForm, TaskForm, WorkspaceForm
from .models import Task, Workspace, WorkspaceMembership
from .permissions import (
    can_view_task,
    get_membership,
    is_supervisor,
    require_membership,
    workspace_member_users,
)
from .services.pdf_service import render_schedule_pdf
from .services.reminder_service import (
    get_due_soon_tasks,
    get_overdue_tasks,
    get_overloaded_days,
    send_reminder_email,
    EmailSendError,
)


def is_ajax(request):
    """True for fetch() requests sent by main.js's modal/table JS.

    Every AJAX-capable view here still works perfectly without this —
    a non-JS browser just gets the normal full-page render/redirect.
    """
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _render_task_table(request):
    # "My tasks" = personal + assigned to me, in one list for me alone.
    tasks = Task.objects.filter(assigned_to=request.user)
    return render_to_string('scheduler/partials/task_table.html', {'tasks': tasks}, request=request)


def _get_task_for_user(request, pk):
    """A task the current user may see, or 404.

    The 404 (not 403) keeps the pre-existing behaviour: guessing a URL
    for someone else's task reveals nothing — not even its existence.
    Permission logic lives in permissions.can_view_task, never in the
    templates.
    """
    task = get_object_or_404(Task, pk=pk)
    if not can_view_task(request.user, task):
        raise Http404('No task matches the given key.')
    return task


def _schedulable_tasks(user):
    """The queryset a generated schedule is built from.

    Completed tasks are finished work, so they are filtered out of the
    queryset *before* the algorithm runs (the algorithm itself is
    untouched). Each schedule covers only this one user's tasks —
    schedules are never merged across people.
    """
    return Task.objects.filter(assigned_to=user).exclude(status=Task.STATUS_COMPLETED)


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
    tasks = Task.objects.filter(assigned_to=request.user)
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

    tasks = Task.objects.filter(assigned_to=request.user)
    due_soon = get_due_soon_tasks(tasks)
    overdue = get_overdue_tasks(tasks)

    if not request.user.email:
        messages.error(request, 'Add an email address to your account to receive reminders.')
    else:
        try:
            sent = send_reminder_email(request.user, due_soon, overdue)
        except EmailSendError:
            # Delivery failed after retries — show a friendly error
            # instead of a 500. The service already logged which
            # recipient and which SMTP error.
            messages.error(
                request,
                f'Could not send the reminder email to {request.user.email} '
                'right now — please try again later.',
            )
        else:
            if sent:
                messages.success(request, f'Reminder email sent to {request.user.email}.')
            else:
                messages.info(request, 'Nothing due soon or overdue — no reminder needed right now.')

    return redirect('dashboard')


@login_required
def task_list(request):
    tasks = Task.objects.filter(assigned_to=request.user)
    return render(request, 'scheduler/task_list.html', {'tasks': tasks})


@login_required
def task_create(request):
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            # New tasks created here are always personal: private,
            # unassigned-elsewhere, and visible only to their creator.
            # Workspace tasks come from the assign flow below.
            task.assigned_to = request.user
            task.assigned_by = None
            task.workspace = None
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
    # Scoped by pk AND the permission rules — a user can never edit
    # another account's task by guessing/changing the URL id, and a
    # non-supervisor can never reassign (the form field is removed
    # server-side for them, so a posted value is simply never read).
    task = _get_task_for_user(request, pk)
    assignable_users = None
    if task.workspace_id is not None and is_supervisor(request.user, task.workspace):
        assignable_users = workspace_member_users(task.workspace)

    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task, assignable_users=assignable_users)
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
        form = TaskForm(instance=task, assignable_users=assignable_users)

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
    # Same server-side permission gate as task_update: 404 unless the
    # permission rules say this user may touch this task.
    task = _get_task_for_user(request, pk)
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
    tasks = _schedulable_tasks(request.user)
    scheduled = generate_schedule(tasks)
    context = {
        'scheduled': scheduled,
        'overloaded_days': get_overloaded_days(tasks),
    }
    return render(request, 'scheduler/schedule.html', context)


@login_required
def schedule_pdf(request):
    tasks = _schedulable_tasks(request.user)
    scheduled = generate_schedule(tasks)
    return render_schedule_pdf(request.user.username, scheduled)


def help_page(request):
    """Static how-to guide for the whole system. Public (like the login
    and register pages) so people can read it before creating an account.
    """
    return render(request, 'scheduler/help.html')


# ---------------------------------------------------------------------------
# Workspaces (multi-user collaboration)
# ---------------------------------------------------------------------------

def _workspace_context(workspace, membership, invite_form=None):
    """Shared context for the workspace detail page."""
    return {
        'workspace': workspace,
        'membership': membership,
        'is_supervisor': membership.role == WorkspaceMembership.ROLE_SUPERVISOR,
        'members': WorkspaceMembership.objects.filter(
            workspace=workspace,
        ).select_related('user'),
        'invite_form': invite_form or InviteMemberForm(),
    }


@login_required
def workspace_list(request):
    """Workspaces the user belongs to, plus the create form.

    Only ever lists the current user's own memberships — a workspace
    you don't belong to is invisible here.
    """
    if request.method == 'POST':
        form = WorkspaceForm(request.POST)
        if form.is_valid():
            workspace = form.save(commit=False)
            workspace.created_by = request.user
            workspace.save()
            WorkspaceMembership.objects.create(
                workspace=workspace,
                user=request.user,
                role=WorkspaceMembership.ROLE_SUPERVISOR,
            )
            messages.success(request, f'Workspace "{workspace.name}" created — you are its supervisor.')
            return redirect('workspace_detail', pk=workspace.pk)
    else:
        form = WorkspaceForm()

    memberships = WorkspaceMembership.objects.filter(
        user=request.user,
    ).select_related('workspace', 'workspace__created_by')
    return render(request, 'scheduler/workspaces.html', {
        'memberships': memberships,
        'form': form,
    })


@login_required
def workspace_detail(request, pk):
    """Members of one workspace + the invite form (supervisors only).

    404 for anyone who is not a member of that workspace.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    membership = require_membership(request.user, workspace)
    return render(
        request,
        'scheduler/workspace_detail.html',
        _workspace_context(workspace, membership),
    )


@login_required
def workspace_invite(request, pk):
    """Add an existing account (by username or email) with a chosen role.

    Server-side: only a supervisor of this workspace may invite, and a
    user can never be added twice (both the form and the model's unique
    constraint enforce it).
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    membership = require_membership(request.user, workspace)

    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can add members.')

    if request.method != 'POST':
        return redirect('workspace_detail', pk=workspace.pk)

    form = InviteMemberForm(request.POST)
    if form.is_valid():
        identifier = form.cleaned_data['identifier'].strip()
        role = form.cleaned_data['role']
        invited = User.objects.filter(
            Q(username=identifier) | Q(email__iexact=identifier),
        ).first()

        if invited is None:
            form.add_error('identifier', 'No account matches that username or email.')
        elif WorkspaceMembership.objects.filter(workspace=workspace, user=invited).exists():
            form.add_error('identifier', f'{invited.username} is already a member of this workspace.')
        else:
            WorkspaceMembership.objects.create(
                workspace=workspace, user=invited, role=role,
            )
            messages.success(
                request,
                f'{invited.username} added to "{workspace.name}" as {role}.',
            )
            return redirect('workspace_detail', pk=workspace.pk)

    return render(
        request,
        'scheduler/workspace_detail.html',
        _workspace_context(workspace, membership, invite_form=form),
    )


@login_required
def assign_task(request, pk):
    """Supervisor-only flow: create a task inside this workspace and
    assign it to one of its members.

    Server-side gates: 404 for non-members, 403 for members who are not
    supervisors, and the assignee picker is restricted to members of
    THIS workspace (posting a non-member is a form validation error).
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can assign tasks.')

    members = workspace_member_users(workspace)

    if request.method == 'POST':
        form = TaskForm(request.POST, assignable_users=members)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.workspace = workspace
            task.assigned_by = request.user
            task.save()
            messages.success(
                request,
                f'"{task.title}" assigned to {task.assigned_to.username}.',
            )
            return redirect('workspace_team_overview', pk=workspace.pk)
    else:
        form = TaskForm(assignable_users=members)

    return render(request, 'scheduler/assign_task.html', {
        'form': form,
        'workspace': workspace,
    })


@login_required
def team_overview(request, pk):
    """Supervisor-only status dashboard for one workspace.

    Lists every member side by side with THEIR workspace tasks' status
    and progress. This is deliberately NOT a schedule: it runs no
    algorithm and never merges anyone's tasks.

    PRIVACY: every query here filters `workspace=ws` explicitly.
    Personal tasks (workspace=None) are structurally impossible to
    match, so no supervisor — on this page or any other — can ever see
    them.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    membership = require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can view the team overview.')

    team = []
    memberships = WorkspaceMembership.objects.filter(
        workspace=workspace,
    ).select_related('user').order_by('joined_at')
    for member in memberships:
        tasks = Task.objects.filter(
            workspace=workspace,   # <- personal tasks can never match
            assigned_to=member.user,
        ).order_by('deadline')
        team.append({'membership': member, 'tasks': tasks})

    return render(request, 'scheduler/team_overview.html', {
        'workspace': workspace,
        'membership': membership,
        'team': team,
    })


# ---------------------------------------------------------------------------
# Workspace management (rename, roles, member removal, leaving, deletion)
#
# Same permission ladder as everywhere else: 404 for non-members (the
# workspace's existence is never leaked), 403 for members who are not
# supervisors, POST-only for every state change.
# ---------------------------------------------------------------------------

def _hand_over_tasks(workspace, from_user, to_user):
    """Reassign `from_user`'s workspace tasks to `to_user`.

    Called when someone is removed from (or leaves) a workspace: their
    workspace tasks must follow the membership, otherwise a departed
    user could keep seeing them (can_view_task grants assignees access).
    Personal tasks (workspace=None) are structurally untouched.
    Returns the number of tasks handed over.
    """
    return Task.objects.filter(
        workspace=workspace, assigned_to=from_user,
    ).update(assigned_to=to_user)


@login_required
def workspace_settings(request, pk):
    """Supervisor-only management page: rename + delete zone."""
    workspace = get_object_or_404(Workspace, pk=pk)
    membership = require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can manage workspace settings.')

    if request.method == 'POST':
        form = WorkspaceForm(request.POST, instance=workspace)
        if form.is_valid():
            form.save()
            messages.success(request, f'Workspace renamed to "{workspace.name}".')
            return redirect('workspace_detail', pk=workspace.pk)
    else:
        form = WorkspaceForm(instance=workspace)

    return render(request, 'scheduler/workspace_settings.html', {
        'workspace': workspace,
        'membership': membership,
        'form': form,
        'member_count': workspace.memberships.count(),
        'task_count': workspace.tasks.count(),
    })


@login_required
def workspace_remove_member(request, pk, membership_pk):
    """Supervisor-only: remove another member from the workspace.

    Server-side rules:
    - 404 for non-members, 403 for members who aren't supervisors.
    - You cannot remove yourself here — "leave workspace" is the flow
      for that (and it has its own last-supervisor guard).
    - The removed member's workspace tasks are handed to the supervisor
      doing the removing, so the work stays in the workspace and the
      departed user immediately loses access to it.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can remove members.')

    target = get_object_or_404(
        WorkspaceMembership, pk=membership_pk, workspace=workspace,
    )

    if request.method != 'POST':
        return redirect('workspace_detail', pk=workspace.pk)

    if target.user_id == request.user.id:
        messages.error(
            request,
            'You cannot remove yourself — use "Leave workspace" instead.',
        )
        return redirect('workspace_detail', pk=workspace.pk)

    removed_user = target.user
    # Safe from orphaning: the remover is a supervisor and is not the
    # target, so at least one supervisor (them) always remains.
    target.delete()
    handed_over = _hand_over_tasks(workspace, removed_user, request.user)

    note = f'{removed_user.username} was removed from "{workspace.name}".'
    if handed_over:
        note += f' {handed_over} task(s) handed to you.'
    messages.success(request, note)
    return redirect('workspace_detail', pk=workspace.pk)


@login_required
def workspace_change_role(request, pk, membership_pk):
    """Supervisor-only: promote a member to supervisor or demote one.

    Guarded so a workspace can never end up with zero supervisors —
    demoting the last supervisor is refused with a message.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can change roles.')

    target = get_object_or_404(
        WorkspaceMembership, pk=membership_pk, workspace=workspace,
    )

    if request.method != 'POST':
        return redirect('workspace_detail', pk=workspace.pk)

    new_role = request.POST.get('role')
    if new_role not in (WorkspaceMembership.ROLE_SUPERVISOR, WorkspaceMembership.ROLE_MEMBER):
        messages.error(request, 'Unknown role.')
        return redirect('workspace_detail', pk=workspace.pk)

    if target.role == new_role:
        messages.info(
            request,
            f'{target.user.username} is already a {target.get_role_display().lower()}.',
        )
        return redirect('workspace_detail', pk=workspace.pk)

    if (
        new_role == WorkspaceMembership.ROLE_MEMBER
        and not WorkspaceMembership.objects.filter(
            workspace=workspace,
            role=WorkspaceMembership.ROLE_SUPERVISOR,
        ).exclude(pk=target.pk).exists()
    ):
        messages.error(
            request,
            'That would leave the workspace with no supervisor — '
            'promote someone else first.',
        )
        return redirect('workspace_detail', pk=workspace.pk)

    target.role = new_role
    target.save(update_fields=['role'])
    messages.success(
        request,
        f'{target.user.username} is now a {target.get_role_display().lower()}.',
    )
    return redirect('workspace_detail', pk=workspace.pk)


@login_required
def workspace_leave(request, pk):
    """Any member may leave; workspace tasks follow them out.

    Guards: the last supervisor may not leave (promote someone else or
    delete the workspace first). The leaver's workspace tasks are handed
    to a remaining supervisor so nothing leaks outside the workspace.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    membership = require_membership(request.user, workspace)

    if request.method != 'POST':
        return redirect('workspace_detail', pk=workspace.pk)

    if membership.role == WorkspaceMembership.ROLE_SUPERVISOR:
        others = WorkspaceMembership.objects.filter(
            workspace=workspace,
            role=WorkspaceMembership.ROLE_SUPERVISOR,
        ).exclude(pk=membership.pk).exists()
        if not others:
            messages.error(
                request,
                'You are the only supervisor of this workspace — promote '
                'another member first, or delete the workspace.',
            )
            return redirect('workspace_detail', pk=workspace.pk)

    successor = WorkspaceMembership.objects.filter(
        workspace=workspace,
        role=WorkspaceMembership.ROLE_SUPERVISOR,
    ).exclude(pk=membership.pk).order_by('joined_at').first()

    membership.delete()
    handed_over = 0
    if successor is not None:
        handed_over = _hand_over_tasks(workspace, request.user, successor.user)

    note = f'You left "{workspace.name}".'
    if handed_over:
        note += f' {handed_over} task(s) handed to {successor.user.username}.'
    messages.success(request, note)
    return redirect('workspace_list')


@login_required
def workspace_delete(request, pk):
    """Supervisor-only: delete a workspace, its memberships and its
    workspace tasks. GET shows a confirmation page stating exactly what
    will be deleted; POST does it. Personal tasks are never touched.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    require_membership(request.user, workspace)
    if not is_supervisor(request.user, workspace):
        raise PermissionDenied('Only a supervisor can delete the workspace.')

    name = workspace.name
    if request.method == 'POST':
        workspace.delete()   # cascades to memberships + workspace tasks
        messages.success(
            request, f'Workspace "{name}" and its workspace tasks were deleted.',
        )
        return redirect('workspace_list')

    return render(request, 'scheduler/workspace_confirm_delete.html', {
        'workspace': workspace,
        'member_count': workspace.memberships.count(),
        'task_count': workspace.tasks.count(),
        'doomed_tasks': workspace.tasks.select_related('assigned_to')[:20],
    })
