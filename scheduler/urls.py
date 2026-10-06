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
]
