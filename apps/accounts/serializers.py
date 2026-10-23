"""账号序列化器。"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import AthleteProfile, CoachProfile, ProfileStatus, UserRole

User = get_user_model()


def profile_summary(user):
    """返回角色 profile 概要（对应 Laravel /auth/me 的 profile 字段）。"""
    profile = user.role_profile()
    if profile is None:
        return None

    if user.role == UserRole.ATHLETE:
        return {
            'id': profile.id,
            'team_id': profile.team_id,
            'jersey_no': profile.jersey_no,
            'position': profile.position,
            'status': profile.status,
            'reject_reason': profile.reject_reason,
        }
    if user.role == UserRole.COACH:
        return {
            'id': profile.id,
            'name': profile.name,
            'phone': profile.phone,
            'status': profile.status,
            'reject_reason': profile.reject_reason,
        }
    if user.role == UserRole.SCHOOL:
        return {'id': profile.id, 'school_id': profile.school_id, 'name': profile.name}
    if user.role == UserRole.CITY:
        return {'id': profile.id, 'city_id': profile.city_id, 'name': profile.name}
    if user.role == UserRole.PROVINCE:
        return {'id': profile.id, 'province_id': profile.province_id, 'name': profile.name}
    return {'id': profile.id, 'name': profile.name}


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'name', 'phone', 'email', 'role', 'role_display', 'status', 'status_display',
            'is_admin', 'permissions', 'profile', 'date_joined',
        )
        read_only_fields = fields

    def get_profile(self, obj):
        return profile_summary(obj)


class RegisterSerializer(serializers.Serializer):
    """个人 / 运动员 / 教练自助注册。"""

    name = serializers.CharField(max_length=50)
    phone = serializers.RegexField(r'^1[3-9]\d{9}$', error_messages={'invalid': '手机号格式不正确'})
    password = serializers.CharField(min_length=6, max_length=64, write_only=True)
    role = serializers.ChoiceField(choices=[UserRole.PERSONAL, UserRole.COACH, UserRole.ATHLETE])

    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError('该手机号已被注册')
        return value

    def create(self, validated_data):
        from django.db import transaction

        role = validated_data['role']
        with transaction.atomic():
            user = User.objects.create_user(
                username=validated_data['phone'],
                phone=validated_data['phone'],
                name=validated_data['name'],
                role=role,
                password=validated_data['password'],
            )
            if role == UserRole.COACH:
                CoachProfile.objects.create(
                    user=user,
                    name=validated_data['name'],
                    phone=validated_data['phone'],
                    status=ProfileStatus.PENDING,
                )
            elif role == UserRole.ATHLETE:
                AthleteProfile.objects.create(
                    user=user,
                    name=validated_data['name'],
                    status=ProfileStatus.PENDING,
                )
        return user


class LoginSerializer(serializers.Serializer):
    """非管理员统一登录：手机号 + 密码。"""

    phone = serializers.CharField(max_length=20)
    password = serializers.CharField(max_length=64, write_only=True)


class AdminLoginSerializer(serializers.Serializer):
    """管理员独立登录：邮箱 + 密码。"""

    email = serializers.EmailField(max_length=191)
    password = serializers.CharField(max_length=64, write_only=True)
