"""认证路由（挂在 /api/ 下）。"""
from django.urls import path

from .views import (
    AdminLoginView,
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    RefreshTokenView,
    RegisterView,
)

urlpatterns = [
    path('auth/register', RegisterView.as_view()),
    path('auth/login', LoginView.as_view()),
    path('admin/auth/login', AdminLoginView.as_view()),
    path('auth/refresh', RefreshTokenView.as_view()),
    path('auth/logout', LogoutView.as_view()),
    path('auth/me', MeView.as_view()),
    path('auth/password', ChangePasswordView.as_view()),
]
