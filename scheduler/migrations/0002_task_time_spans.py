# Flexible time spans (deadline gains a time of day; duration becomes a
# value + unit pair). The data copy below preserves every existing task:
# old durations were whole minutes, so they migrate as
# duration_value=<old minutes>, duration_unit='minutes'.

from django.db import migrations, models

# Minutes per unit, duplicated here because migrations must be frozen
# snapshots and historical models do not carry Task.duration_minutes.
UNIT_MINUTES = {
    'minutes': 1,
    'hours': 60,
    'days': 1440,
    'weeks': 7 * 1440,
    'months': 30 * 1440,
}


def copy_duration_to_value_unit(apps, schema_editor):
    Task = apps.get_model('scheduler', 'Task')
    for task in Task.objects.all().iterator():
        Task.objects.filter(pk=task.pk).update(
            duration_value=task.duration,
            duration_unit='minutes',
        )


def restore_duration_from_value_unit(apps, schema_editor):
    Task = apps.get_model('scheduler', 'Task')
    for task in Task.objects.all().iterator():
        Task.objects.filter(pk=task.pk).update(
            duration=task.duration_value * UNIT_MINUTES[task.duration_unit],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0001_initial'),
    ]

    operations = [
        # Deadline: date -> date and time (legacy rows keep midnight).
        migrations.AlterField(
            model_name='task',
            name='deadline',
            field=models.DateTimeField(help_text='The date and time this task is due.'),
        ),
        # New pair; duration_value starts with a placeholder default so
        # the old column's values can be copied before it is dropped.
        migrations.AddField(
            model_name='task',
            name='duration_value',
            field=models.PositiveIntegerField(default=0, help_text='Estimated duration, as a number of the unit below.'),
        ),
        migrations.AddField(
            model_name='task',
            name='duration_unit',
            field=models.CharField(
                choices=[
                    ('minutes', 'Minutes'), ('hours', 'Hours'), ('days', 'Days'),
                    ('weeks', 'Weeks'), ('months', 'Months'),
                ],
                default='minutes',
                help_text='Unit for the duration value.',
                max_length=10,
            ),
        ),
        migrations.RunPython(copy_duration_to_value_unit, restore_duration_from_value_unit),
        # Drop the placeholder default so the migration state matches the
        # model exactly (duration_value has no default in models.py).
        migrations.AlterField(
            model_name='task',
            name='duration_value',
            field=models.PositiveIntegerField(help_text='Estimated duration, as a number of the unit below.'),
        ),
        # Make the old column nullable for a moment so this migration can
        # be reversed on a database that already has rows: reversing
        # recreates `duration` nullable, fills it from the value+unit pair,
        # then restores NOT NULL below.
        migrations.AlterField(
            model_name='task',
            name='duration',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.RunPython(migrations.RunPython.noop, restore_duration_from_value_unit),
        migrations.RemoveField(
            model_name='task',
            name='duration',
        ),
    ]
