from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'priority', 'deadline', 'duration')
    list_filter = ('priority',)
    search_fields = ('title', 'user__username')
