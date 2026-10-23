"""赛事直播模型：matches / match_referees / match_events / match_files / face_verify_records。"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.org.models import GroupType


class MatchStatus(models.TextChoices):
    NOT_STARTED = 'not_started', '未开始'
    LIVE = 'live', '进行中'
    FINISHED = 'finished', '已结束'


class EventType(models.TextChoices):
    GOAL = 'goal', '进球'
    YELLOW = 'yellow', '黄牌'
    RED = 'red', '红牌'
    SUBSTITUTION = 'substitution', '换人'
    TEXT = 'text', '文字'


class RefereeRole(models.TextChoices):
    CHIEF = 'chief', '主裁'
    ASSISTANT = 'assistant', '边裁'
    FOURTH = 'fourth', '第四官员'


class MatchFileType(models.TextChoices):
    SIGN_IN = 'sign_in', '培训签到表'
    RECORD_CONFIRM = 'record_confirm', '记录确认单'
    REFEREE_REPORT = 'referee_report', '裁判报告单'


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class Match(models.Model):
    group_type = models.CharField('组别', max_length=32, choices=GroupType.choices)
    home_team_name = models.CharField('主队名称', max_length=100)
    away_team_name = models.CharField('客队名称', max_length=100)
    home_team_logo = models.CharField('主队 Logo', max_length=255, null=True, blank=True)
    away_team_logo = models.CharField('客队 Logo', max_length=255, null=True, blank=True)
    home_team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='home_matches'
    )
    away_team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='away_matches'
    )
    match_time = models.DateTimeField('比赛时间')
    venue = models.CharField('比赛场地', max_length=200, null=True, blank=True)
    status = models.CharField('状态', max_length=16, choices=MatchStatus.choices, default=MatchStatus.NOT_STARTED)
    home_score = models.PositiveSmallIntegerField('主队比分', default=0)
    away_score = models.PositiveSmallIntegerField('客队比分', default=0)
    lineup_home = models.JSONField('主队阵容', null=True, blank=True)
    lineup_away = models.JSONField('客队阵容', null=True, blank=True)
    live_link = models.CharField('直播链接', max_length=512, null=True, blank=True)
    version = models.PositiveIntegerField('乐观锁版本号', default=0)
    created_by = models.BigIntegerField('创建人', null=True, blank=True)
    updated_by = models.BigIntegerField('更新人', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField('删除时间', null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'matches'
        ordering = ['-match_time', '-id']
        indexes = [
            models.Index(fields=['group_type', 'status', 'match_time']),
            models.Index(fields=['status', 'match_time']),
            models.Index(fields=['home_team', 'status', 'match_time']),
            models.Index(fields=['away_team', 'status', 'match_time']),
        ]

    def __str__(self):
        return f'{self.home_team_name} vs {self.away_team_name}'

    @property
    def is_finished(self) -> bool:
        return self.status == MatchStatus.FINISHED


class MatchReferee(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='referees')
    role = models.CharField('角色', max_length=16, choices=RefereeRole.choices)
    name = models.CharField('姓名', max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'match_referees'
        constraints = [models.UniqueConstraint(fields=['match', 'role', 'name'], name='uniq_match_referee')]

    def __str__(self):
        return f'{self.get_role_display()}:{self.name}'


class MatchEvent(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='events')
    type = models.CharField('事件类型', max_length=16, choices=EventType.choices)
    minute = models.PositiveSmallIntegerField('比赛分钟')
    team_side = models.CharField('主客队', max_length=8, null=True, blank=True)
    athlete = models.ForeignKey(
        'accounts.AthleteProfile', null=True, blank=True, on_delete=models.SET_NULL, related_name='events'
    )
    player_name = models.CharField('球员姓名', max_length=100, null=True, blank=True)
    player_no = models.CharField('球衣号', max_length=16, null=True, blank=True)
    related_player_name = models.CharField('助攻/换上球员', max_length=100, null=True, blank=True)
    content = models.CharField('文字内容', max_length=500, null=True, blank=True)
    client_token = models.CharField('防重令牌', max_length=64, unique=True)
    created_by = models.BigIntegerField('创建人', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'match_events'
        ordering = ['minute', 'id']
        indexes = [
            models.Index(fields=['match', 'type']),
            models.Index(fields=['match', 'minute', 'id']),
            models.Index(fields=['athlete', 'type']),
        ]


class MatchFile(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='files')
    type = models.CharField('文件类型', max_length=32, choices=MatchFileType.choices)
    file_name = models.CharField('原始文件名', max_length=255)
    file_path = models.CharField('私有存储相对路径', max_length=255)
    mime = models.CharField('MIME', max_length=64)
    size = models.BigIntegerField('大小(字节)')
    uploaded_by = models.BigIntegerField('上传人', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'match_files'
        indexes = [models.Index(fields=['match', 'type'])]


class FaceVerifyRecord(models.Model):
    class Result(models.TextChoices):
        PASS = 'pass', '通过'
        FAIL = 'fail', '未通过'
        PENDING = 'pending', '待定'

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='face_verify_records')
    player_name = models.CharField('球员姓名', max_length=50)
    player_no = models.CharField('球衣号', max_length=16, null=True, blank=True)
    photo_path = models.CharField('照片路径', max_length=255, null=True, blank=True)
    result = models.CharField('结果', max_length=16, choices=Result.choices)
    score = models.DecimalField('相似度', max_digits=5, decimal_places=2, null=True, blank=True)
    channel = models.CharField('渠道', max_length=32, default='mock')
    request_id = models.CharField('外部请求 ID', max_length=255, null=True, blank=True)
    remark = models.CharField('备注', max_length=500, null=True, blank=True)
    created_by = models.BigIntegerField('创建人', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'face_verify_records'
        ordering = ['-id']
