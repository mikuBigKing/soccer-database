"""报名业务模型：赛事 / 报名单 / 报名日志。

报名流转：
    draft(草稿) → submitted(待市州审核) → city_approved(市州通过待报送)
    → reported(已报送待省审) → approved(省级通过)
任意审核环节驳回 → rejected，并记录 reject_level(city|province) + reject_reason；
驳回后提交方可重新提交（重新进入 submitted）。

资料挂载口径（与用户确认）：
- 盖章报名表扫描件 → Registration.stamped_form_*
- 学籍验证材料、球员头像 → accounts.AthleteProfile
- 教练资质附件 → accounts.CoachProfile
文件均落私有目录（settings.PRIVATE_STORAGE_ROOT），下载走鉴权接口。
"""
from django.conf import settings
from django.db import models


class Competition(models.Model):
    """赛事（一届/一项比赛）。"""

    class Status(models.TextChoices):
        OPEN = 'open', '报名中'
        CLOSED = 'closed', '已截止'

    name = models.CharField('赛事名称', max_length=100)
    year = models.PositiveSmallIntegerField('年份')
    status = models.CharField('状态', max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'competitions'
        ordering = ['-year', '-id']

    def __str__(self):
        return f'{self.year} {self.name}'


class Registration(models.Model):
    """报名单：一个球队在一届赛事下的一份报名。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        SUBMITTED = 'submitted', '待市州审核'
        CITY_APPROVED = 'city_approved', '市州通过待报送'
        REPORTED = 'reported', '已报送待省审'
        APPROVED = 'approved', '省级通过'
        REJECTED = 'rejected', '已驳回'

    class RejectLevel(models.TextChoices):
        CITY = 'city', '市州驳回'
        PROVINCE = 'province', '省级驳回'

    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name='registrations')
    team = models.ForeignKey('org.Team', on_delete=models.CASCADE, related_name='registrations')
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='registrations'
    )
    status = models.CharField('状态', max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    reject_level = models.CharField('驳回方', max_length=20, choices=RejectLevel.choices, null=True, blank=True)
    reject_reason = models.TextField('驳回原因', null=True, blank=True)
    stamped_form_path = models.CharField('盖章报名表扫描件路径', max_length=255, null=True, blank=True)
    stamped_form_name = models.CharField('盖章报名表扫描件文件名', max_length=255, null=True, blank=True)
    submitted_at = models.DateTimeField('提交时间', null=True, blank=True)
    city_reviewed_by = models.BigIntegerField('市州审核人 user_id', null=True, blank=True)
    city_reviewed_at = models.DateTimeField('市州审核时间', null=True, blank=True)
    reported_by = models.BigIntegerField('报送人 user_id', null=True, blank=True)
    reported_at = models.DateTimeField('报送时间', null=True, blank=True)
    province_reviewed_by = models.BigIntegerField('省级审核人 user_id', null=True, blank=True)
    province_reviewed_at = models.DateTimeField('省级审核时间', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'registrations'
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['competition', 'team'], name='uniq_registration_competition_team')
        ]
        indexes = [
            models.Index(fields=['team', 'status']),
            models.Index(fields=['competition', 'status']),
        ]

    def __str__(self):
        return f'{self.competition} - {self.team}'

    @property
    def school(self):
        return self.team.school

    @property
    def city(self):
        return self.team.school.city


class RegistrationLog(models.Model):
    """报名日志：审核 / 提交报送 / 账号与资料操作全量留痕。"""

    class Action(models.TextChoices):
        CREATE = 'create', '创建报名单'
        SUBMIT = 'submit', '提交报名'
        CITY_APPROVE = 'city_approve', '市州通过'
        CITY_REJECT = 'city_reject', '市州驳回'
        REPORT = 'report', '市州报送'
        PROVINCE_APPROVE = 'province_approve', '省级通过'
        PROVINCE_REJECT = 'province_reject', '省级驳回'
        MATERIAL_UPLOAD = 'material_upload', '资料上传'
        AVATAR_UPLOAD = 'avatar_upload', '头像上传'
        FORM_DOWNLOAD = 'form_download', '报名表Word下载'
        EXPORT = 'export', '报名情况导出'
        ACCOUNT_CREATE = 'account_create', '账号创建'
        ACCOUNT_UPDATE = 'account_update', '账号修改'
        ACCOUNT_DELETE = 'account_delete', '账号删除'
        IDENTITY_UPDATE = 'identity_update', '身份信息修改'

    registration = models.ForeignKey(
        Registration, null=True, blank=True, on_delete=models.SET_NULL, related_name='logs'
    )
    action = models.CharField('动作', max_length=30, choices=Action.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    detail = models.JSONField('详情', default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'registration_logs'
        ordering = ['-id']
        indexes = [
            models.Index(fields=['registration', 'action']),
            models.Index(fields=['actor', 'action']),
        ]
