from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import FirstUser


@admin.register(FirstUser)
class FirstUserAdmin(UserAdmin):

    ordering = ('email',)

    list_display = (
        'email',
        'full_name',
        'phone_number',
        'is_staff',
        'is_active',
    )

    search_fields = (
        'email',
        'full_name',
        'phone_number',
    )

    fieldsets = (
        (None, {
            'fields': ('email', 'password')
        }),
        ('Personal Information', {
            'fields': ('full_name', 'phone_number')
        }),
        ('Permissions', {
            'fields': (
                'is_active',
                'is_staff',
                'is_superuser',
                'groups',
                'user_permissions',
            )
        }),
        ('Important Dates', {
            'fields': (
                'last_login',
                'date_joined',
                'created_at',
                'updated_at',
            )
        }),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email',
                'full_name',
                'phone_number',
                'password1',
                'password2',
                'is_staff',
                'is_active',
            ),
        }),
    )

    readonly_fields = ('created_at', 'updated_at')