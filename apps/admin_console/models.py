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
