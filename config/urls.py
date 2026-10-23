"""项目根路由。

- /api/auth/*            注册 / 统一登录 / 管理员登录 / 登出 / me
- /api/public/live/*     赛事直播公众端（免登）
- /api/admin/live/*      赛事直播管理端（登录 + 权限点）
- /api/admin/*           行政区划/城市/学校（管理员）
- /api/city/*            市级查阅
- /api/athlete/*         运动员端
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.common.views import HealthView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health', HealthView.as_view()),
    path('api/', include('apps.accounts.urls')),
    path('api/public/live/', include('apps.live.urls_public')),
    path('api/admin/live/', include('apps.live.urls_admin')),
    path('api/admin/', include('apps.org.urls')),
    path('api/', include('apps.org.urls_city')),
    path('api/athlete/', include('apps.athlete.urls')),
    path('api/', include('apps.registration.urls')),
    path('api/city/', include('apps.registration.urls_city')),
    path('api/province/', include('apps.registration.urls_province')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
