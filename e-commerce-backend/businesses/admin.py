from django.contrib import admin

from .models import Business, BusinessMembership


class BusinessMembershipInline(admin.TabularInline):
    model = BusinessMembership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "default_currency", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "slug")
    inlines = [BusinessMembershipInline]


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "role", "status", "created_at")
    list_filter = ("role", "status", "business")
    search_fields = ("user__email", "business__name")
    autocomplete_fields = ["user", "business", "invited_by"]
