"""管理端操作审计：只记脱敏快照，不落明文敏感字段。"""
from django.conf import settings
from django.db import models


class AdminOperationLog(models.Model):
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+', verbose_name='操作人',
    )
    action = models.CharField('动作', max_length=32)
    target_type = models.CharField('对象类型', max_length=16)
    target_id = models.BigIntegerField('对象 ID')
    before = models.JSONField('变更前快照', null=True, blank=True)
    after = models.JSONField('变更后快照', null=True, blank=True)
    ip = models.CharField('来源 IP', max_length=64, blank=True, default='')
    trace_id = models.CharField('链路 ID', max_length=64, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'admin_operation_logs'
        ordering = ('-id',)
        indexes = [models.Index(fields=['target_type', 'target_id'], name='idx_admin_op_target')]


class CoachObjection(models.Model):
    """教练公示期异议（管理员代录）。"""

    coach = models.ForeignKey(
        'accounts.CoachProfile', on_delete=models.CASCADE,
        related_name='objections', verbose_name='教练员',
    )
    submitter_name = models.CharField('提交人', max_length=50)
    contact = models.CharField('联系方式', max_length=50)
    content = models.TextField('异议内容')
    handled = models.BooleanField('已处理', default=False, db_index=True)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)

    class Meta:
        db_table = 'coach_objections'
        ordering = ('-id',)
        indexes = [models.Index(fields=['coach', 'handled'])]


class PlayerSuspension(models.Model):
    """球员停赛记录：红黄牌自动生成或管理员人工处理。"""

    class Source(models.TextChoices):
        AUTO = 'auto', '红黄牌累计自动'
        MANUAL = 'manual', '人工处理'

    class Status(models.TextChoices):
        ACTIVE = 'active', '生效中'
        REVOKED = 'revoked', '已撤销'
        EXPIRED = 'expired', '已结束'

    player = models.ForeignKey(
        'accounts.AthleteProfile', on_delete=models.CASCADE,
        related_name='suspensions', verbose_name='球员',
    )
    reason = models.CharField('停赛原因', max_length=200)
    matches_count = models.PositiveSmallIntegerField('停赛场次')
    start_at = models.DateTimeField('停赛开始')
    end_at = models.DateTimeField('停赛结束', null=True, blank=True)
    source = models.CharField('来源', max_length=10, choices=Source.choices)
    source_events = models.JSONField('关联红黄牌事件id', default=list, blank=True)
    prev_eligibility = models.CharField(
        '停赛前资格状态', max_length=16, null=True, blank=True,
    )
    status = models.CharField(
        '状态', max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True,
    )
    created_by = models.BigIntegerField('创建人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'player_suspensions'
        ordering = ('-id',)
        indexes = [models.Index(fields=['player', 'status'])]


class PlayerTeamChange(models.Model):
    """球员球队归属变更历史（含首次绑定与解绑）。"""

    player = models.ForeignKey(
        'accounts.AthleteProfile', on_delete=models.CASCADE,
        related_name='team_changes', verbose_name='球员',
    )
    from_team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='原球队',
    )
    to_team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='新球队',
    )
    reason = models.CharField('变更原因', max_length=200)
    changed_by = models.BigIntegerField('操作人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'player_team_changes'
        ordering = ('-id',)
        indexes = [models.Index(fields=['player', 'created_at'])]
