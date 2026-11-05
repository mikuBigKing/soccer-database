"""赛事运营后台数据模型。

与既有 live 模块的关系：
- live.Match             —— eventops.MatchEvent/MatchDocument/MatchRecord 都外键挂它
- live.MatchReferee      —— eventops.Referee + MatchOfficial 是它的增强版（裁判主档 + 外键安排）
- live.MatchEvent        —— 仅直播/设备事件（字符串球员 + 少类型），eventops.MatchEvent 是运营代录全量表
- live.MatchFile         —— 单版本归档；eventops.MatchDocument 是分册 + 版本链 + 归档锁

所有 eventops 表统一前缀 ``eventops_``。
"""
from django.conf import settings
from django.db import models


class RefereeLevel(models.TextChoices):
    NATIONAL = 'national', '国家级'
    FIRST = 'first', '一级'
    SECOND = 'second', '二级'
    THIRD = 'third', '三级'


class RefereeGender(models.TextChoices):
    MALE = 'male', '男'
    FEMALE = 'female', '女'


class OfficialRole(models.TextChoices):
    CHIEF = 'chief', '主裁'
    ASSISTANT_1 = 'assistant_1', '第一边裁'
    ASSISTANT_2 = 'assistant_2', '第二边裁'
    FOURTH = 'fourth', '第四官员'
    VAR = 'var', 'VAR'


class EventType(models.TextChoices):
    """事件类型（覆盖 live.EventType 并补齐运营代录所需）。"""

    GOAL = 'goal', '进球'
    PENALTY_GOAL = 'penalty_goal', '点球进球'
    PENALTY_MISSED = 'penalty_missed', '点球未进'
    OWN_GOAL = 'own_goal', '乌龙球'
    YELLOW = 'yellow', '黄牌'
    RED = 'red', '红牌'
    SECOND_YELLOW = 'second_yellow', '两黄变红'
    FOUL = 'foul', '犯规'


class EventPeriod(models.TextChoices):
    FIRST = 'first', '上半场'
    SECOND = 'second', '下半场'
    EXTRA_1 = 'extra_1', '加时上半场'
    EXTRA_2 = 'extra_2', '加时下半场'
    SHOOTOUT = 'shootout', '点球大战'


class DocType(models.TextChoices):
    SIGN_IN = 'sign_in', '培训签到表'
    RECORD_CONFIRM = 'record_confirm', '记录确认单'
    REFEREE_REPORT = 'referee_report', '裁判报告单'


class DocStatus(models.TextChoices):
    ACTIVE = 'active', '正常'
    ARCHIVED = 'archived', '已归档'


class Referee(models.Model):
    """裁判员主档。"""

    name = models.CharField('姓名', max_length=50, db_index=True)
    gender = models.CharField('性别', max_length=10, choices=RefereeGender.choices, default='', blank=True)
    level = models.CharField('裁判等级', max_length=20, choices=RefereeLevel.choices, db_index=True)
    certificate_no = models.CharField('裁判证号', max_length=50, unique=True, db_index=True)
    phone = models.CharField('联系电话', max_length=20, blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'eventops_referee'
        verbose_name = '裁判员'
        verbose_name_plural = '裁判员'
        ordering = ['id']

    def __str__(self):
        return f'{self.name}({self.get_level_display()})'


class MatchOfficial(models.Model):
    """比赛裁判安排（外键到 Referee，live.MatchReferee 的增强版）。"""

    match = models.ForeignKey('live.Match', on_delete=models.CASCADE, related_name='eventops_officials')
    referee = models.ForeignKey(Referee, on_delete=models.PROTECT, related_name='officials')
    role = models.CharField('角色', max_length=20, choices=OfficialRole.choices, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'eventops_match_official'
        verbose_name = '比赛裁判安排'
        unique_together = [('match', 'role'), ('match', 'referee')]
        ordering = ['match_id', 'role']

    def __str__(self):
        return f'{self.match} / {self.referee.name} / {self.get_role_display()}'


class MatchEvent(models.Model):
    """赛事事件全量记录（运营代录核心表）。"""

    match = models.ForeignKey('live.Match', on_delete=models.CASCADE, related_name='eventops_events')
    event_type = models.CharField('事件类型', max_length=20, choices=EventType.choices, db_index=True)
    period = models.CharField('比赛阶段', max_length=20, choices=EventPeriod.choices, db_index=True)
    minute = models.PositiveSmallIntegerField('比赛分钟', help_text='1-120')
    stoppage_minute = models.PositiveSmallIntegerField('补时分钟', default=0, help_text='0-10')
    team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='eventops_events',
    )
    athlete = models.ForeignKey(
        'accounts.AthleteProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='eventops_events',
    )
    related_athlete = models.ForeignKey(
        'accounts.AthleteProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='关联球员（助攻/换上）',
    )
    description = models.CharField('备注', max_length=255, blank=True, default='')

    version = models.PositiveIntegerField('乐观锁版本号', default=1)
    recorded_source = models.CharField('录入来源', max_length=20, default='ops', db_index=True)

    # 软删除
    is_deleted = models.BooleanField('是否已删除', default=False, db_index=True)
    deleted_at = models.DateTimeField('删除时间', null=True, blank=True)
    deleted_by = models.BigIntegerField('删除人 user_id', null=True, blank=True)
    delete_reason = models.CharField('删除原因', max_length=255, blank=True, default='')

    created_by = models.BigIntegerField('创建人 user_id', null=True, blank=True)
    updated_by = models.BigIntegerField('更新人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'eventops_match_event'
        verbose_name = '比赛事件'
        ordering = ['match_id', 'period', 'minute', 'id']
        indexes = [
            models.Index(fields=['match', 'is_deleted'], name='idx_eve_match_del'),
            models.Index(fields=['match', 'event_type'], name='idx_eve_match_type'),
            models.Index(fields=['team', 'is_deleted'], name='idx_eve_team_del'),
            models.Index(fields=['athlete', 'is_deleted'], name='idx_eve_ath_del'),
        ]

    def __str__(self):
        return f'{self.match} / {self.get_event_type_display()} / {self.minute}\''


class EventRevision(models.Model):
    """事件版本快照（可追溯）。"""

    event = models.ForeignKey(MatchEvent, on_delete=models.CASCADE, related_name='revisions')
    version_no = models.PositiveIntegerField('快照版本号')
    change_type = models.CharField(
        '变更类型', max_length=20, choices=[
            ('create', '新增'), ('update', '修改'), ('delete', '删除'),
            ('restore', '恢复'), ('finalize_lock', '归档锁定'),
        ],
    )
    snapshot = models.JSONField('事件快照（JSON）', default=dict)
    change_reason = models.CharField('变更原因', max_length=255, blank=True, default='')
    changed_by = models.BigIntegerField('操作人 user_id', null=True, blank=True)
    trace_id = models.CharField('链路 ID', max_length=40, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'eventops_event_revision'
        verbose_name = '事件版本快照'
        unique_together = [('event', 'version_no')]
        ordering = ['event_id', 'version_no']


class MatchDocument(models.Model):
    """赛事文件档案（分册 + 版本链 + 归档锁）。"""

    match = models.ForeignKey('live.Match', on_delete=models.CASCADE, related_name='eventops_documents')
    doc_type = models.CharField('文件类型', max_length=20, choices=DocType.choices, db_index=True)
    part_key = models.CharField('分册', max_length=20, blank=True, default='main', db_index=True)
    title = models.CharField('档案标题', max_length=200, blank=True, default='')
    status = models.CharField('状态', max_length=16, choices=DocStatus.choices, default=DocStatus.ACTIVE, db_index=True)
    current_version_no = models.PositiveIntegerField('当前版本号', default=1)
    archived_at = models.DateTimeField('归档时间', null=True, blank=True)
    archived_by = models.BigIntegerField('归档人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'eventops_match_document'
        verbose_name = '赛事文件档案'
        unique_together = [('match', 'doc_type', 'part_key')]
        ordering = ['match_id', 'doc_type', 'part_key']
        indexes = [
            models.Index(fields=['match', 'doc_type', 'status'], name='idx_doc_match_type_status'),
        ]

    def __str__(self):
        return f'{self.match} / {self.get_doc_type_display()} / {self.part_key}'


class MatchDocumentVersion(models.Model):
    """文件档案版本。"""

    document = models.ForeignKey(MatchDocument, on_delete=models.CASCADE, related_name='versions')
    version_no = models.PositiveIntegerField('版本号')
    file_path = models.CharField('私有存储相对路径', max_length=255)
    original_name = models.CharField('原始文件名', max_length=255)
    file_ext = models.CharField('扩展名', max_length=16, blank=True, default='')
    mime_type = models.CharField('MIME 类型', max_length=64)
    file_size = models.BigIntegerField('文件大小(字节)')
    sha256 = models.CharField('SHA-256', max_length=64, db_index=True)
    change_note = models.CharField('版本说明', max_length=255, blank=True, default='')
    uploaded_by = models.BigIntegerField('上传人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'eventops_document_version'
        verbose_name = '档案版本'
        unique_together = [('document', 'version_no')]
        ordering = ['document_id', 'version_no']


class MatchRecord(models.Model):
    """赛事数据归档锁（与 live.Match 一对一）。"""

    match = models.OneToOneField('live.Match', on_delete=models.CASCADE, related_name='eventops_record')
    events_locked = models.BooleanField('事件已锁定', default=False, db_index=True)
    locked_at = models.DateTimeField('锁定时间', null=True, blank=True)
    locked_by = models.BigIntegerField('锁定人 user_id', null=True, blank=True)
    lock_note = models.CharField('归档说明', max_length=255, blank=True, default='')
    # 取消归档
    unlocked_at = models.DateTimeField('取消归档时间', null=True, blank=True)
    unlocked_by = models.BigIntegerField('取消人 user_id', null=True, blank=True)
    unlock_reason = models.CharField('取消原因', max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'eventops_match_record'
        verbose_name = '赛事归档状态'

    def __str__(self):
        return f'{self.match} / locked={self.events_locked}'


class OperationLog(models.Model):
    """运营后台统一操作日志。"""

    action = models.CharField('动作', max_length=40, db_index=True)
    resource_type = models.CharField('资源类型', max_length=40, db_index=True)
    resource_id = models.BigIntegerField('资源主键', db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='eventops_logs',
    )
    user_role = models.CharField('操作人角色', max_length=20, blank=True, default='')
    user_name = models.CharField('操作人姓名', max_length=50, blank=True, default='')
    before_data = models.JSONField('变更前数据', null=True, blank=True)
    after_data = models.JSONField('变更后数据', null=True, blank=True)
    ip = models.CharField('客户端 IP', max_length=64, blank=True, default='')
    trace_id = models.CharField('链路 ID', max_length=40, blank=True, default='', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'eventops_operation_log'
        verbose_name = '操作日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['resource_type', 'resource_id'], name='idx_oper_res'),
            models.Index(fields=['user_role'], name='idx_oper_role'),
            models.Index(fields=['action'], name='idx_oper_act'),
        ]

    def __str__(self):
        return f'{self.action} {self.resource_type}#{self.resource_id}'
