from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path(
        'login/',
        auth_views.LoginView.as_view(template_name='registration/login.html'),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.RegisterView.as_view(), name='register'),

    # Dashboard (also serves as the "/" home for authenticated users)
    path('', views.dashboard, name='dashboard'),
    path('reminders/send/', views.send_reminder_now, name='send_reminder_now'),

    # Tasks
    path('tasks/', views.task_list, name='task_list'),
    path('tasks/add/', views.task_create, name='task_create'),
    path('tasks/<int:pk>/edit/', views.task_update, name='task_update'),
    path('tasks/<int:pk>/delete/', views.task_delete, name='task_delete'),

    # Schedule
    path('schedule/', views.schedule_view, name='schedule'),
    path('schedule/pdf/', views.schedule_pdf, name='schedule_pdf'),

    # Workspaces (multi-user collaboration)
    path('workspaces/', views.workspace_list, name='workspace_list'),
    path('workspaces/<int:pk>/', views.workspace_detail, name='workspace_detail'),
    path('workspaces/<int:pk>/invite/', views.workspace_invite, name='workspace_invite'),
    path('workspaces/<int:pk>/assign/', views.assign_task, name='workspace_assign_task'),
    path('workspaces/<int:pk>/team/', views.team_overview, name='workspace_team_overview'),

    # Workspace management (rename, roles, member removal, leave, delete)
    path('workspaces/<int:pk>/settings/', views.workspace_settings, name='workspace_settings'),
    path(
        'workspaces/<int:pk>/members/<int:membership_pk>/remove/',
        views.workspace_remove_member, name='workspace_remove_member',
    ),
    path(
        'workspaces/<int:pk>/members/<int:membership_pk>/role/',
        views.workspace_change_role, name='workspace_change_role',
    ),
    path('workspaces/<int:pk>/leave/', views.workspace_leave, name='workspace_leave'),
    path('workspaces/<int:pk>/delete/', views.workspace_delete, name='workspace_delete'),

    # Help
    path('help/', views.help_page, name='help'),
]
