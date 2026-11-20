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
    if user.role == UserRole.COUNTY:
        return {'id': profile.id, 'city_id': profile.city_id, 'county': profile.county, 'name': profile.name}
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
    """个人 / 运动员 / 教练自助注册：邮箱 + 邮箱验证码。"""

    name = serializers.CharField(max_length=50)
    email = serializers.EmailField(max_length=191)
    password = serializers.CharField(min_length=6, max_length=64, write_only=True)
    role = serializers.ChoiceField(choices=[UserRole.PERSONAL, UserRole.COACH, UserRole.ATHLETE])
    code = serializers.CharField(max_length=6, write_only=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def validate(self, attrs):
        from .models import VerificationCode
        from .verification import normalize_email, verify_code

        request = self.context.get('request')
        verify_code(request, normalize_email(attrs['email']),
                    VerificationCode.Scene.REGISTER, attrs['code'])
        return attrs

    def create(self, validated_data):
        from django.db import transaction

        role = validated_data['role']
        email = validated_data['email']
        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                name=validated_data['name'],
                role=role,
                password=validated_data['password'],
            )
            if role == UserRole.COACH:
                CoachProfile.objects.create(
                    user=user,
                    name=validated_data['name'],
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
    """统一登录：邮箱 + 密码。"""

    email = serializers.EmailField(max_length=191)
    password = serializers.CharField(max_length=64, write_only=True)

    def validate_email(self, value):
        return value.strip().lower()


class EmailSendSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=191)
    scene = serializers.ChoiceField(choices=['register', 'reset_password', 'change_password', 'change_email'])

    def validate_email(self, value):
        return value.strip().lower()


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=191)
    code = serializers.CharField(max_length=6, write_only=True)
    new_password = serializers.CharField(min_length=6, max_length=64, write_only=True)

    def validate_email(self, value):
        return value.strip().lower()


class EmailChangeSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=191)
    code = serializers.CharField(max_length=6, write_only=True)

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        from .models import User as _User

        if _User.objects.filter(email__iexact=attrs['email']).exists():
            raise serializers.ValidationError({'email': '该邮箱已被其他账号使用'})
        return attrs


class AdminLoginSerializer(serializers.Serializer):
    """管理员独立登录：邮箱 + 密码。"""

    email = serializers.EmailField(max_length=191)
    password = serializers.CharField(max_length=64, write_only=True)
