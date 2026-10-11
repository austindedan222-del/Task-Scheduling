"""Server-side authorization for workspaces and task visibility.

Every rule here is enforced in views and querysets — never by merely
hiding a link in a template. The privacy rule that ties it all
together: a task with workspace=None is PERSONAL and is invisible to
everyone except its owner, including supervisors. Supervisor-facing
queries must always filter by an explicit workspace (workspace=ws),
which structurally excludes personal tasks.
"""

from django.http import Http404

from .models import WorkspaceMembership


def get_membership(user, workspace):
    """The user's membership in the workspace, or None."""
    if user is None or not user.is_authenticated:
        return None
    return WorkspaceMembership.objects.filter(
        workspace=workspace, user=user,
    ).first()


def is_supervisor(user, workspace):
    """True only for a membership whose role is 'supervisor'."""
    membership = get_membership(user, workspace)
    return membership is not None and membership.role == WorkspaceMembership.ROLE_SUPERVISOR


def require_membership(user, workspace):
    """The user's membership, or 404 if they are not a member.

    Non-members get 404 (not 403) so a workspace's existence is not
    leaked to outsiders.
    """
    membership = get_membership(user, workspace)
    if membership is None:
        raise Http404('No workspace matches the given key.')
    return membership


def workspace_member_users(workspace):
    """User queryset of everyone who belongs to the workspace."""
    from django.contrib.auth.models import User

    return User.objects.filter(
        workspace_memberships__workspace=workspace,
    ).order_by('username')


def can_view_task(user, task):
    """Who may see a task, enforced server-side in every view.

    - Personal task (workspace=None): only its assignee (the creator).
      Nobody else — not even a supervisor, on any page or query.
    - Workspace task: the user it is assigned to, plus any supervisor
      OF THAT workspace.
    """
    if user is None or not user.is_authenticated:
        return False
    if task.workspace_id is None:
        return task.assigned_to_id == user.id
    if task.assigned_to_id == user.id:
        return True
    return is_supervisor(user, task.workspace)


def can_edit_task(user, task):
    """Who may edit a task.

    Same population as can_view_task: a member edits what is assigned
    to them, a supervisor edits any workspace task, and nobody edits a
    personal task but its owner. Reassignment is gated separately —
    only supervisors get the assigned_to form field (see TaskForm).
    """
    return can_view_task(user, task)
