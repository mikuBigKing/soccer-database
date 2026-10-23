from django.contrib import admin

from .models import City, Province, School, Team

admin.site.register([Province, City, School, Team])
