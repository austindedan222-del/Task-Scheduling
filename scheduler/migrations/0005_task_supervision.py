# Supervision: every existing task keeps working after this migration —
# assigned_to is backfilled from the current owner (user), then made
# non-null. assigned_by stays null, which is exactly the "personal task"
# signal the permission rules rely on.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_assigned_to(apps, schema_editor):
    """Existing tasks belong to their current owner."""
    Task = apps.get_model('scheduler', 'Task')
    for task in Task.objects.filter(assigned_to__isnull=True).iterator():
        Task.objects.filter(pk=task.pk).update(assigned_to_id=task.user_id)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduler', '0004_workspaces'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='assigned_by',
            field=models.ForeignKey(blank=True, help_text='Who assigned it; null for personal tasks.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks_assigned', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='task',
            name='assigned_to',
            field=models.ForeignKey(help_text='Whose task it is: the creator for personal tasks, the chosen member for workspace tasks.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(backfill_assigned_to, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='task',
            name='assigned_to',
            field=models.ForeignKey(help_text='Whose task it is: the creator for personal tasks, the chosen member for workspace tasks.', on_delete=django.db.models.deletion.CASCADE, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL),
        ),
    ]
