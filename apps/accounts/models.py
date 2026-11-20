"""用户与 8 角色 profile 模型（对应 Laravel users + *_profiles 迁移）。

账号方案 C：users 主表 + 各角色 profile 表。
- 管理员用 email + password 登录（独立登录页）
- 其余 7 种角色用 phone + password 登录（统一登录页）
- 区县级（county）不设独立端，并入市州端使用，数据范围按区县裁剪
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class UserRole(models.TextChoices):
    ADMIN = 'admin', '管理员'
    PROVINCE = 'province', '省级'
    CITY = 'city', '市级'
    COUNTY = 'county', '区县级'
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


class VerifyStatus(models.TextChoices):
    UNVERIFIED = 'unverified', '未验证'
    VERIFIED = 'verified', '已验证'
    FAILED = 'failed', '验证不通过'


class Gender(models.TextChoices):
    MALE = 'male', '男'
    FEMALE = 'female', '女'


class EligibilityStatus(models.TextChoices):
    ELIGIBLE = 'eligible', '可参赛'
    SUSPENDED = 'suspended', '停赛中'
    DISQUALIFIED = 'disqualified', '取消资格'


class User(AbstractUser):
    """主账号表。

    username 作为登录唯一标识：统一取 email，
    仅用于满足 AbstractUser 的唯一约束，不对外暴露。
    """

    name = models.CharField('姓名', max_length=50, blank=True, default='')
    email = models.EmailField('邮箱', max_length=191, unique=True, null=True, blank=True)
    email_verified_at = models.DateTimeField('邮箱验证时间', null=True, blank=True)
    phone = models.CharField('手机号', max_length=20, unique=True, null=True, blank=True)
    role = models.CharField('角色', max_length=20, choices=UserRole.choices, default=UserRole.PERSONAL, db_index=True)
    status = models.CharField('账号状态', max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE, db_index=True)
    permissions = models.JSONField('权限点', default=list, blank=True)

    # 数据权限范围（阶段三）
    scope_school = models.ForeignKey(
        'org.School', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='管辖学校',
    )
    scope_city = models.ForeignKey(
        'org.City', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='管辖城市',
    )
    scope_province = models.ForeignKey(
        'org.Province', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='管辖省份',
    )

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
            UserRole.COUNTY: 'county_profile',
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


class CountyProfile(models.Model):
    """区县级账号：并入市州端使用，数据范围按 city + county 裁剪。"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='county_profile')
    city = models.ForeignKey('org.City', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    county = models.CharField('区县名称', max_length=50, null=True, blank=True)
    name = models.CharField('姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'county_profiles'


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
    id_card = models.CharField('身份证号', max_length=18, null=True, blank=True)
    qualification_path = models.CharField('资质附件路径', max_length=255, null=True, blank=True)
    qualification_name = models.CharField('资质附件文件名', max_length=255, null=True, blank=True)
    status = models.CharField('审核状态', max_length=20, choices=ProfileStatus.choices, default=ProfileStatus.PENDING, db_index=True)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField('是否公示', default=False, db_index=True)
    deleted_at = models.DateTimeField('软删除时间', null=True, blank=True)
    public_start_at = models.DateTimeField('公示开始', null=True, blank=True)
    public_end_at = models.DateTimeField('公示结束', null=True, blank=True)
    unpublish_reason = models.CharField('取消公示原因', max_length=200, null=True, blank=True)

    class Meta:
        db_table = 'coach_profiles'
        indexes = [models.Index(fields=['status'])]
        constraints = [
            # 身份证全局唯一，但已软删档案不占用（同证号可重新建档）
            models.UniqueConstraint(
                fields=['id_card'],
                condition=Q(deleted_at__isnull=True) & Q(id_card__isnull=False),
                name='uniq_coach_id_card',
            ),
        ]

    def save(self, *args, **kwargs):
        # 空身份证统一存 NULL，避免唯一索引把空串当成重复值
        if not self.id_card:
            self.id_card = None
        super().save(*args, **kwargs)


class AthleteProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='athlete_profile')
    team = models.ForeignKey('org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='athletes')
    name = models.CharField('姓名', max_length=50)
    jersey_no = models.PositiveSmallIntegerField('球衣号码', null=True, blank=True)
    position = models.CharField('位置', max_length=20, null=True, blank=True)
    id_card = models.CharField('身份证号', max_length=18, null=True, blank=True)
    avatar_path = models.CharField('头像路径', max_length=255, null=True, blank=True)
    avatar_name = models.CharField('头像文件名', max_length=255, null=True, blank=True)
    enrollment_cert_path = models.CharField('学籍验证材料路径', max_length=255, null=True, blank=True)
    enrollment_cert_name = models.CharField('学籍验证材料文件名', max_length=255, null=True, blank=True)
    status = models.CharField('审核状态', max_length=20, choices=ProfileStatus.choices, default=ProfileStatus.PENDING, db_index=True)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField('是否公示', default=False, db_index=True)
    deleted_at = models.DateTimeField('软删除时间', null=True, blank=True)
    school = models.ForeignKey(
        'org.School', null=True, blank=True, on_delete=models.SET_NULL, related_name='athletes_direct',
    )
    gender = models.CharField(
        '性别', max_length=10, choices=Gender.choices, null=True, blank=True,
    )
    grade = models.CharField('年级', max_length=20, null=True, blank=True)
    height_cm = models.PositiveSmallIntegerField('身高(cm)', null=True, blank=True)
    weight_kg = models.PositiveSmallIntegerField('体重(kg)', null=True, blank=True)
    real_name_status = models.CharField(
        '实名认证', max_length=20, choices=VerifyStatus.choices,
        default=VerifyStatus.UNVERIFIED, db_index=True,
    )
    enrollment_status = models.CharField(
        '学籍验证', max_length=20, choices=VerifyStatus.choices,
        default=VerifyStatus.UNVERIFIED, db_index=True,
    )
    eligibility_status = models.CharField(
        '参赛资格', max_length=20, choices=EligibilityStatus.choices,
        default=EligibilityStatus.ELIGIBLE, db_index=True,
    )

    class Meta:
        db_table = 'athlete_profiles'
        indexes = [models.Index(fields=['team', 'status'])]
        constraints = [
            # 同队球衣号唯一，跨队可重复；已软删球员不占用号码
            models.UniqueConstraint(
                fields=['team', 'jersey_no'],
                condition=Q(deleted_at__isnull=True) & Q(team__isnull=False) & Q(jersey_no__isnull=False),
                name='uniq_athlete_team_jersey',
            ),
            # 身份证全局唯一（软删档案不占用）
            models.UniqueConstraint(
                fields=['id_card'],
                condition=Q(deleted_at__isnull=True) & Q(id_card__isnull=False),
                name='uniq_athlete_id_card',
            ),
        ]

    def save(self, *args, **kwargs):
        # 空身份证统一存 NULL，避免唯一索引把空串当成重复值
        if not self.id_card:
            self.id_card = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class VerificationCode(models.Model):
    """邮箱验证码（登录/注册/改密/换绑共用）。"""

    class Scene(models.TextChoices):
        REGISTER = 'register', '注册'
        RESET_PASSWORD = 'reset_password', '忘记密码'
        CHANGE_PASSWORD = 'change_password', '修改密码'
        CHANGE_EMAIL = 'change_email', '绑定/换绑邮箱'

    email = models.CharField('邮箱', max_length=191, db_index=True)
    scene = models.CharField('场景', max_length=32, choices=Scene.choices, db_index=True)
    code_hash = models.CharField('验证码哈希', max_length=128)
    expires_at = models.DateTimeField('过期时间')
    used_at = models.DateTimeField('使用时间', null=True, blank=True)
    attempts = models.PositiveSmallIntegerField('校验失败次数', default=0)
    ip = models.CharField('来源 IP', max_length=64, blank=True, default='')
    trace_id = models.CharField('链路 ID', max_length=64, blank=True, default='')
    created_at = models.DateTimeField('发送时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'verification_codes'
        ordering = ('-id',)
        indexes = [models.Index(fields=['email', 'scene', 'created_at'])]
