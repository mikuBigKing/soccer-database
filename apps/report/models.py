"""赛事报告模型：一场比赛一份报告。"""
from django.db import models


class ReportStatus(models.TextChoices):
    PENDING = 'pending', '待生成'
    GENERATING = 'generating', '生成中'
    SUCCEEDED = 'succeeded', '已生成'
    FAILED = 'failed', '生成失败'


class MatchReport(models.Model):
    match = models.OneToOneField('live.Match', on_delete=models.CASCADE, related_name='report')
    status = models.CharField('状态', max_length=16, choices=ReportStatus.choices, default=ReportStatus.PENDING)

    narrative = models.TextField('报告全文', blank=True, default='')
    chapters = models.JSONField('分章内容', default=list, blank=True)
    key_moments = models.JSONField('关键节点', default=list, blank=True)
    highlights = models.JSONField('高光点', default=list, blank=True)
    data_notes = models.JSONField('数据说明', default=list, blank=True)
    confidence = models.PositiveSmallIntegerField('置信度', null=True, blank=True)

    fingerprint = models.CharField('输入指纹', max_length=64, blank=True, default='')
    prompt_version = models.CharField('Prompt 版本', max_length=32, blank=True, default='')
    model_name = models.CharField('模型名称', max_length=64, blank=True, default='')

    input_tokens = models.IntegerField('输入 tokens', null=True, blank=True)
    output_tokens = models.IntegerField('输出 tokens', null=True, blank=True)
    duration_ms = models.IntegerField('耗时(毫秒)', null=True, blank=True)
    error_msg = models.CharField('失败原因', max_length=500, null=True, blank=True)

    generated_by = models.BigIntegerField('生成人', null=True, blank=True)
    generated_at = models.DateTimeField('生成时间', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'match_reports'
        verbose_name = '比赛报告'
        verbose_name_plural = '比赛报告'
        indexes = [models.Index(fields=['status'])]

    def __str__(self):
        return f'report#{self.match_id}:{self.status}'

    @property
    def has_content(self) -> bool:
        return bool(self.narrative)
