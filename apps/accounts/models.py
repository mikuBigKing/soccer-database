"""用户与 7 角色 profile 模型（对应 Laravel users + *_profiles 迁移）。

账号方案 C：users 主表 + 各角色 profile 表。
- 管理员用 email + password 登录（独立登录页）
- 其余 6 种角色用 phone + password 登录（统一登录页）
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserRole(models.TextChoices):
    ADMIN = 'admin', '管理员'
    PROVINCE = 'province', '省级'
    CITY = 'city', '市级'
    SCHOOL = 'school', '校级'
    COACH = 'coach', '教练'
    ATHLETE = 'athlete', '运动员'
    PERSONAL = 'personal', '个人'


class UserStatus(models.TextChoices):
    ACTIVE = 'active', '正常'
    DISABLED = 'disabled', '已禁用'


class ProfileStatus(models.TextChoices):
    PENDING = 'pending', '待审核'
    APPROVED = 'approved', '已通过'
    REJECTED = 'rejected', '已驳回'


class User(AbstractUser):
    """主账号表。

    username 作为登录唯一标识：管理员取 email，其余角色取 phone，
    仅用于满足 AbstractUser 的唯一约束，不对外暴露。
    """

    name = models.CharField('姓名', max_length=50, blank=True, default='')
    email = models.EmailField('邮箱', max_length=191, unique=True, null=True, blank=True)
    phone = models.CharField('手机号', max_length=20, unique=True, null=True, blank=True)
    role = models.CharField('角色', max_length=20, choices=UserRole.choices, default=UserRole.PERSONAL, db_index=True)
    status = models.CharField('账号状态', max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE, db_index=True)
    permissions = models.JSONField('权限点', default=list, blank=True)

    class Meta:
        db_table = 'users'
        verbose_name = '用户'
        verbose_name_plural = '用户'

    def __str__(self):
        return self.name or self.username

    def save(self, *args, **kwargs):
        # 空邮箱统一存 NULL，避免多人共用 '' 触发唯一约束
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def is_active_account(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def role_profile(self):
        """按角色返回对应 profile 实例（无则为 None）。"""
        mapping = {
            UserRole.ADMIN: 'admin_profile',
            UserRole.PROVINCE: 'province_profile',
            UserRole.CITY: 'city_profile',
            UserRole.SCHOOL: 'school_profile',
            UserRole.COACH: 'coach_profile',
            UserRole.ATHLETE: 'athlete_profile',
        }
        attr = mapping.get(self.role)
        return getattr(self, attr, None) if attr else None


class AdminProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')
    name = models.CharField('管理员姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'admin_profiles'


class ProvinceProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='province_profile')
    province = models.ForeignKey('org.Province', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    name = models.CharField('姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'province_profiles'


class CityProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='city_profile')
    city = models.ForeignKey('org.City', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    name = models.CharField('姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'city_profiles'


class SchoolProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='school_profile')
    school = models.ForeignKey('org.School', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    name = models.CharField('姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'school_profiles'


class CoachProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='coach_profile')
    name = models.CharField('姓名', max_length=50)
    phone = models.CharField('联系电话', max_length=20, null=True, blank=True)
    status = models.CharField('审核状态', max_length=20, choices=ProfileStatus.choices, default=ProfileStatus.PENDING, db_index=True)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'coach_profiles'
        indexes = [models.Index(fields=['status'])]


class AthleteProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='athlete_profile')
    team = models.ForeignKey('org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='athletes')
    name = models.CharField('姓名', max_length=50)
    jersey_no = models.PositiveSmallIntegerField('球衣号码', null=True, blank=True)
    position = models.CharField('位置', max_length=20, null=True, blank=True)
    status = models.CharField('审核状态', max_length=20, choices=ProfileStatus.choices, default=ProfileStatus.PENDING, db_index=True)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'athlete_profiles'
        indexes = [models.Index(fields=['team', 'status'])]

    def __str__(self):
        return self.name
