"""行政区划与组织模型（provinces / cities / schools / teams）。"""
from django.conf import settings
from django.db import models


class GroupType(models.TextChoices):
    UNIVERSITY = 'university', '高校组'
    MIDDLE_SCHOOL = 'middle_school', '中学组'
    PRIMARY_SCHOOL = 'primary_school', '小学组'


class TeamStatus(models.TextChoices):
    PENDING = 'pending', '待审核'
    APPROVED = 'approved', '已通过'
    REJECTED = 'rejected', '已驳回'


class Province(models.Model):
    name = models.CharField('省份名称', max_length=50)
    code = models.CharField('行政区划编码', max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'provinces'
        ordering = ['code']

    def __str__(self):
        return self.name


class City(models.Model):
    province = models.ForeignKey(Province, on_delete=models.CASCADE, related_name='cities')
    name = models.CharField('城市名称', max_length=50)
    code = models.CharField('行政区划编码', max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cities'
        ordering = ['code']
        constraints = [models.UniqueConstraint(fields=['province', 'code'], name='uniq_city_province_code')]

    def __str__(self):
        return self.name


class School(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', '正常'
        DISABLED = 'disabled', '已停用'

    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='schools')
    name = models.CharField('学校名称', max_length=150)
    logo = models.CharField('Logo 路径', max_length=255, null=True, blank=True)
    address = models.CharField('地址', max_length=255, null=True, blank=True)
    status = models.CharField('状态', max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.BigIntegerField('创建人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'schools'
        ordering = ['id']
        indexes = [models.Index(fields=['city', 'status'])]

    def __str__(self):
        return self.name


class Team(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='teams')
    coach = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='coached_teams')
    name = models.CharField('球队名称', max_length=150)
    logo = models.CharField('Logo 路径', max_length=255, null=True, blank=True)
    group_type = models.CharField('组别', max_length=30, choices=GroupType.choices)
    status = models.CharField('审核状态', max_length=20, choices=TeamStatus.choices, default=TeamStatus.PENDING)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'teams'
        ordering = ['id']
        indexes = [
            models.Index(fields=['school', 'status']),
            models.Index(fields=['coach', 'status']),
        ]

    def __str__(self):
        return self.name
