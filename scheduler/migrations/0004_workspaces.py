# Multi-user collaboration: workspaces, memberships, and the nullable
# Task.workspace FK. A NULL workspace means a personal, fully private
# task — only rows with a workspace are ever visible to a supervisor.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduler', '0003_task_percent_complete_task_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='Workspace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspaces_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='task',
            name='workspace',
            field=models.ForeignKey(blank=True, help_text="Null = personal task, private to its owner. Only tasks inside a workspace are visible to that workspace's supervisor.", null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='scheduler.workspace'),
        ),
        migrations.CreateModel(
            name='WorkspaceMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('supervisor', 'Supervisor'), ('member', 'Member')], max_length=20)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_memberships', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='scheduler.workspace')),
            ],
            options={
                'ordering': ['joined_at'],
                'unique_together': {('workspace', 'user')},
            },
        ),
    ]
