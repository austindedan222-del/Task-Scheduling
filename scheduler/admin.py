from django.contrib import admin

from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'user', 'priority', 'deadline',
        'duration_value', 'duration_unit', 'status', 'percent_complete',
    )
    list_filter = ('priority', 'status')
    search_fields = ('title', 'user__username')
