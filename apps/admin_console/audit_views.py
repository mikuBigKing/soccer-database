"""全局审计日志查询与导出（/api/admin/audit-logs）。"""

import csv
import io
from datetime import datetime

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.formatting import format_dt
from apps.common.pagination import paginate
from apps.common.permissions import role_permission

from .models import AuditLog

ADMIN_ONLY = [IsAuthenticated, role_permission('admin')]

CSV_HEADER = ('id', '发生时间', '操作人', '角色', '模块', '动作', '对象类型', '对象ID',
              'IP', 'trace_id', '变更前', '变更后')


class AuditQuerySerializer(serializers.Serializer):
    module = serializers.CharField(required=False, allow_blank=True)
    action = serializers.CharField(required=False, allow_blank=True)
    resource_type = serializers.CharField(required=False, allow_blank=True)
    resource_id = serializers.IntegerField(required=False)
    actor_id = serializers.IntegerField(required=False)
    actor_role = serializers.CharField(required=False, allow_blank=True)
    keyword = serializers.CharField(required=False, allow_blank=True)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)


def _filtered(params: dict):
    qs = (AuditLog.objects.select_related('actor')
          .order_by('-id'))
    if params.get('module'):
        qs = qs.filter(module=params['module'])
    if params.get('action'):
        qs = qs.filter(action__icontains=params['action'])
    if params.get('resource_type'):
        qs = qs.filter(resource_type=params['resource_type'])
    if params.get('resource_id'):
        qs = qs.filter(resource_id=params['resource_id'])
    if params.get('actor_id'):
        qs = qs.filter(actor_id=params['actor_id'])
    if params.get('actor_role'):
        qs = qs.filter(actor_role=params['actor_role'])
    if params.get('keyword'):
        from django.db.models import Q

        keyword = params['keyword']
        qs = qs.filter(
            Q(action__icontains=keyword) | Q(module__icontains=keyword)
            | Q(resource_type__icontains=keyword) | Q(actor__name__icontains=keyword)
            | Q(actor__phone__icontains=keyword) | Q(trace_id__icontains=keyword)
        )
    if params.get('date_from'):
        qs = qs.filter(created_at__date__gte=params['date_from'])
    if params.get('date_to'):
        qs = qs.filter(created_at__date__lte=params['date_to'])
    return qs


def _serialize(row: AuditLog) -> dict:
    return {
        'id': row.id,
        'created_at': format_dt(row.created_at),
        'actor_id': row.actor_id,
        'actor_name': row.actor.name if row.actor_id else None,
        'actor_role': row.actor_role,
        'module': row.module,
        'action': row.action,
        'resource_type': row.resource_type,
        'resource_id': row.resource_id,
        'ip': row.ip,
        'trace_id': row.trace_id,
        'before': row.before,
        'after': row.after,
    }


class AuditLogListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = AuditQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = paginate(_filtered(serializer.validated_data), request)
        data['list'] = [_serialize(row) for row in data['list']]
        return Response(data)


class AuditLogExportView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        serializer = AuditQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        rows = list(_filtered(serializer.validated_data)[:10000])

        buffer = io.StringIO()
        buffer.write('\ufeff')
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([
                row.id,
                format_dt(row.created_at),
                row.actor.name if row.actor_id else '',
                row.actor_role,
                row.module,
                row.action,
                row.resource_type,
                row.resource_id or '',
                row.ip,
                row.trace_id,
                str(row.before) if row.before else '',
                str(row.after) if row.after else '',
            ])

        filename = f"audit-logs-{timezone.now().strftime('%Y%m%d%H%M%S')}.csv"
        response = StreamingHttpResponse(iter([buffer.getvalue()]), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
