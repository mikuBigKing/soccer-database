from django.contrib import admin

from .models import AdminProfile, AthleteProfile, CityProfile, CoachProfile, ProvinceProfile, SchoolProfile, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'name', 'phone', 'email', 'role', 'status')
    list_filter = ('role', 'status')
    search_fields = ('username', 'name', 'phone', 'email')


for model in (AdminProfile, ProvinceProfile, CityProfile, SchoolProfile, CoachProfile, AthleteProfile):
    admin.site.register(model)
