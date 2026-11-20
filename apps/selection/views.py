"""遴选接口：管理端 /api/admin/selection/*，前台 /api/portal/selection-*。"""
import io

from django.http import HttpResponse
from openpyxl import Workbook
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import paginate
from apps.common.permissions import role_permission
from apps.org.models import GroupType

from . import services
from .models import SelectionScore

ADMIN_ONLY = [IsAuthenticated, role_permission('admin')]


class ProfileQuerySerializer(serializers.Serializer):
    competition_id = serializers.IntegerField(required=False)
    group_type = serializers.ChoiceField(choices=GroupType.choices, required=False)
    team_id = serializers.IntegerField(required=False)
    status = serializers.CharField(required=False, allow_blank=True)
    id_card = serializers.CharField(required=False, allow_blank=True)
    keyword = serializers.CharField(required=False, allow_blank=True)


class ImportProfilesSerializer(serializers.Serializer):
    competition_id = serializers.IntegerField()
    group_type = serializers.ChoiceField(choices=GroupType.choices, required=False, allow_blank=True)
    team_id = serializers.IntegerField(required=False)


class ScoreQuerySerializer(serializers.Serializer):
    competition_id = serializers.IntegerField(required=False)
    group_type = serializers.ChoiceField(choices=GroupType.choices, required=False)
    team_id = serializers.IntegerField(required=False)
    stage = serializers.ChoiceField(choices=SelectionScore.Stage.choices, required=False)


class ScoreWriteSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField()
    stage = serializers.ChoiceField(choices=SelectionScore.Stage.choices)
    goals_score = serializers.IntegerField(min_value=0, max_value=100)
    defense_score = serializers.IntegerField(min_value=0, max_value=100)
    assist_score = serializers.IntegerField(min_value=0, max_value=100)
    foul_score = serializers.IntegerField(min_value=0, max_value=100)
    remark = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')


class ScoreUpdateSerializer(ScoreWriteSerializer):
    profile_id = serializers.IntegerField(required=False)
    stage = serializers.ChoiceField(choices=SelectionScore.Stage.choices, required=False)
    goals_score = serializers.IntegerField(min_value=0, max_value=100, required=False)
    defense_score = serializers.IntegerField(min_value=0, max_value=100, required=False)
    assist_score = serializers.IntegerField(min_value=0, max_value=100, required=False)
    foul_score = serializers.IntegerField(min_value=0, max_value=100, required=False)


class BestQuerySerializer(serializers.Serializer):
    competition_id = serializers.IntegerField(required=False)
    group_type = serializers.ChoiceField(choices=GroupType.choices, required=False)
    published = serializers.BooleanField(required=False)


class GenerateBestSerializer(serializers.Serializer):
    competition_id = serializers.IntegerField()
    group_type = serializers.ChoiceField(choices=GroupType.choices)
    top_n = serializers.IntegerField(min_value=1, max_value=30, required=False, default=11)


class PublishBestSerializer(serializers.Serializer):
    competition_id = serializers.IntegerField()
    group_type = serializers.ChoiceField(choices=GroupType.choices)
    published = serializers.BooleanField(required=False, default=True)


# ---------------------------------------------------------------------------
# 管理端
# ---------------------------------------------------------------------------

class ProfileListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        params = ProfileQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = paginate(services.profile_queryset(params.validated_data), request)
        data['list'] = [services.serialize_profile(p) for p in data['list']]
        return Response(data)

    def post(self, request):
        """按赛事报名单导入遴选档案。"""
        serializer = ImportProfilesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.import_profiles(request, **serializer.validated_data))


class ScoreListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        params = ScoreQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = paginate(services.score_queryset(params.validated_data), request)
        data['list'] = [services.score_row(s) for s in data['list']]
        return Response(data)

    def post(self, request):
        serializer = ScoreWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.upsert_score(request, serializer.validated_data))


class ScoreDetailView(APIView):
    permission_classes = ADMIN_ONLY

    def put(self, request, pk: int):
        serializer = ScoreUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        score = SelectionScore.objects.filter(pk=pk).first()
        if score is None:
            from apps.common.codes import ResponseCode
            from apps.common.exceptions import BusinessException

            raise BusinessException(ResponseCode.NOT_FOUND, '评分不存在', status_code=404)
        payload = {
            'profile_id': score.profile_id,
            'stage': score.stage,
            **{f: data.get(f, getattr(score, f)) for f in
               ('goals_score', 'defense_score', 'assist_score', 'foul_score')},
            'remark': data.get('remark', score.remark),
        }
        return Response(services.upsert_score(request, payload))


class ScoreSummaryView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        params = ScoreQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response(services.summary(params.validated_data))


class ScoreExportView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        params = ScoreQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        rows = list(services.score_queryset(params.validated_data)[:5000])

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '评分汇总'
        sheet.append(['序号', '姓名', '球队', '组别', '阶段', '进球', '防守', '助攻',
                      '犯规', '总分', '评委', '更新时间'])
        for index, score in enumerate(rows, start=1):
            row = services.score_row(score)
            sheet.append([
                index, row['name'], row['team_name'] or '', row['group_type'],
                row['stage_label'], row['goals_score'], row['defense_score'],
                row['assist_score'], row['foul_score'], row['total_score'],
                row['judge_name'] or '', row['updated_at'],
            ])

        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="selection-scores.xlsx"'
        return response


class BestListView(APIView):
    permission_classes = ADMIN_ONLY

    def get(self, request):
        params = BestQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        rows = services.best_queryset(params.validated_data)
        return Response({'total': len(rows), 'list': [services.best_row(r) for r in rows]})


class BestGenerateView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = GenerateBestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.generate_best(request, **serializer.validated_data))


class BestPublishView(APIView):
    permission_classes = ADMIN_ONLY

    def post(self, request):
        serializer = PublishBestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.publish_best(request, **serializer.validated_data))


# ---------------------------------------------------------------------------
# 前台（与 api/selection.ts 契约对齐）
# ---------------------------------------------------------------------------

class PortalScoreView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = ScoreQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = paginate(services.score_queryset(params.validated_data), request)
        data['list'] = [services.score_row(s) for s in data['list']]
        return Response(data)


class PortalBestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = BestQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        params.validated_data['published'] = True
        rows = services.best_queryset(params.validated_data)
        return Response({'total': len(rows), 'list': [services.best_row(r) for r in rows]})
