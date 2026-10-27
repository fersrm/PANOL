from django.contrib import admin

from .models import Position, Profile


class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user_FK",)


admin.site.register(Profile, ProfileAdmin)


class PositionAdmin(admin.ModelAdmin):
    list_display = ("user_position",)


admin.site.register(Position, PositionAdmin)
