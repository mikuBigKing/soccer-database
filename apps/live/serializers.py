"""赛事直播序列化器：输出资源 + 入参校验。

输出字段严格对齐前端 `src/features/live/types/live.ts`（该文件由 Laravel Resource 推导），
命名统一为 `*_label`，`match_time` 与审计时间为 `Y-m-d H:i:s`。
"""
from django.conf import settings
from rest_framework import serializers

from apps.common.formatting import format_dt
from apps.org.models import GroupType, Team

from .models import (
    EventType,
    FaceVerifyRecord,
    Match,
    MatchEvent,
    MatchFile,
    MatchFileType,
    MatchReferee,
    MatchStatus,
    RefereeRole,
)

GROUP_TYPES = [c[0] for c in GroupType.choices]


class MatchListSerializer(serializers.ModelSerializer):
    group_type_label = serializers.CharField(source='get_group_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    match_time = serializers.SerializerMethodField()
    competition_id = serializers.IntegerField(read_only=True)
    competition_name = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = (
            'id', 'competition_id', 'competition_name', 'group_type', 'group_type_label',
            'home_team_name', 'away_team_name',
            'home_team_logo', 'away_team_logo', 'match_time', 'venue',
            'status', 'status_label', 'home_score', 'away_score',
        )

    def get_match_time(self, obj):
        return format_dt(obj.match_time)

    def get_competition_name(self, obj):
        return obj.competition.name if obj.competition_id else None


class MatchDetailSerializer(MatchListSerializer):
    class Meta(MatchListSerializer.Meta):
        fields = MatchListSerializer.Meta.fields + ('lineup_home', 'lineup_away', 'live_link')


class AdminMatchDetailSerializer(MatchDetailSerializer):
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta(MatchDetailSerializer.Meta):
        fields = MatchDetailSerializer.Meta.fields + (
            'version', 'created_by', 'updated_by', 'created_at', 'updated_at',
        )

    def get_created_at(self, obj):
        return format_dt(obj.created_at)

    def get_updated_at(self, obj):
        return format_dt(obj.updated_at)


class MatchEventSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source='get_type_display', read_only=True)
    player_no = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = MatchEvent
        fields = (
            'id', 'type', 'type_label', 'minute', 'team_side', 'player_name', 'player_no',
            'related_player_name', 'content', 'created_at',
        )

    def get_player_no(self, obj):
        # 前端契约：number | null
        if obj.player_no in (None, ''):
            return None
        try:
            return int(obj.player_no)
        except (TypeError, ValueError):
            return None

    def get_created_at(self, obj):
        return format_dt(obj.created_at)


class RefereeSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = MatchReferee
        fields = ('id', 'role', 'role_label', 'name')


class MatchFileSerializer(serializers.ModelSerializer):
    match_id = serializers.IntegerField(read_only=True)
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = MatchFile
        fields = (
            'id', 'match_id', 'type', 'file_name', 'file_path', 'mime', 'size',
            'uploaded_by', 'created_at',
        )

    def get_created_at(self, obj):
        return format_dt(obj.created_at)


class FaceVerifyRecordSerializer(serializers.ModelSerializer):
    match_id = serializers.IntegerField(read_only=True)
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = FaceVerifyRecord
        fields = (
            'id', 'match_id', 'player_name', 'player_no', 'photo_path', 'result', 'score',
            'channel', 'request_id', 'remark', 'created_by', 'created_at',
        )

    def get_created_at(self, obj):
        return format_dt(obj.created_at)


# ---------------------------------------------------------------------------
# 入参校验
# ---------------------------------------------------------------------------

class MatchStoreSerializer(serializers.Serializer):
    group_type = serializers.ChoiceField(choices=GROUP_TYPES)
    home_team_name = serializers.CharField(max_length=100)
    away_team_name = serializers.CharField(max_length=100)
    home_team_logo = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    away_team_logo = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    match_time = serializers.DateTimeField()
    venue = serializers.CharField(max_length=200, required=False, allow_null=True, allow_blank=True)
    competition_id = serializers.IntegerField(required=False, allow_null=True)
    home_team_id = serializers.IntegerField(required=False, allow_null=True)
    away_team_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_competition_id(self, value):
        if value is None:
            return value
        from apps.registration.models import Competition

        if not Competition.objects.filter(pk=value).exists():
            raise serializers.ValidationError('赛事不存在')
        return value

    def validate_home_team_id(self, value):
        if value is None:
            return value
        if not Team.objects.filter(pk=value).exists():
            raise serializers.ValidationError('主队不存在')
        return value

    def validate_away_team_id(self, value):
        if value is None:
            return value
        if not Team.objects.filter(pk=value).exists():
            raise serializers.ValidationError('客队不存在')
        return value

    def validate(self, attrs):
        home = attrs.get('home_team_id')
        away = attrs.get('away_team_id')
        if home and away and home == away:
            raise serializers.ValidationError({'away_team_id': '主队与客队不能相同'})
        return attrs


class MatchUpdateSerializer(MatchStoreSerializer):
    version = serializers.IntegerField(min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # version 作为乐观锁必填，其余业务字段可部分更新
        for name, field in self.fields.items():
            if name != 'version':
                field.required = False


class MatchStatusSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['start', 'finish'])
    version = serializers.IntegerField(min_value=0)


class LineupPlayerSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    no = serializers.CharField(max_length=16)
    position = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    is_captain = serializers.BooleanField(required=False, default=False)


class LineupSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=0)
    lineup_home = serializers.ListField(child=LineupPlayerSerializer(), allow_empty=True)
    lineup_away = serializers.ListField(child=LineupPlayerSerializer(), allow_empty=True)


class EventStoreSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[c[0] for c in EventType.choices])
    minute = serializers.IntegerField(min_value=0, max_value=130)
    team_side = serializers.ChoiceField(choices=['home', 'away'], required=False, allow_null=True)
    player_name = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    player_no = serializers.CharField(max_length=16, required=False, allow_null=True, allow_blank=True)
    related_player_name = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    content = serializers.CharField(max_length=500, required=False, allow_null=True, allow_blank=True)
    client_token = serializers.CharField(max_length=64)
    athlete_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_player_no(self, value):
        if value in (None, ''):
            return value
        text = str(value).strip()
        if not text.isdigit() or not 0 <= int(text) <= 99:
            raise serializers.ValidationError('球衣号必须为 0-99 的数字')
        return str(int(text))

    def validate_athlete_id(self, value):
        if value is None:
            return value
        from apps.accounts.models import AthleteProfile

        if not AthleteProfile.objects.filter(pk=value).exists():
            raise serializers.ValidationError('运动员不存在')
        return value

    def validate(self, attrs):
        event_type = attrs.get('type')
        if event_type != EventType.TEXT:
            if not attrs.get('team_side'):
                raise serializers.ValidationError({'team_side': '该事件类型必须指定主客队'})
            if not attrs.get('player_name'):
                raise serializers.ValidationError({'player_name': '该事件类型必须指定球员'})
        if event_type == EventType.SUBSTITUTION and not attrs.get('related_player_name'):
            raise serializers.ValidationError({'related_player_name': '换人事件必须指定换上球员'})
        if event_type == EventType.TEXT and not attrs.get('content'):
            raise serializers.ValidationError({'content': '文字事件必须填写内容'})
        return attrs


class EventUpdateSerializer(EventStoreSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 更新不允许修改幂等键（F-06）
        self.fields.pop('client_token', None)
        for field in self.fields.values():
            field.required = False

    def validate(self, attrs):
        # 更新为部分字段，规则按最终值判定由 service 负责
        return attrs


class RefereeItemSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[c[0] for c in RefereeRole.choices])
    name = serializers.CharField(max_length=50)


class RefereesSerializer(serializers.Serializer):
    referees = serializers.ListField(child=RefereeItemSerializer(), allow_empty=True, max_length=4)


class MatchFileUploadSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[c[0] for c in MatchFileType.choices])
    file = serializers.FileField()


class LiveLinkSerializer(serializers.Serializer):
    live_link = serializers.URLField(max_length=512, required=False, allow_null=True, allow_blank=True)


class FaceVerifySerializer(serializers.Serializer):
    player_name = serializers.CharField(max_length=50)
    player_no = serializers.CharField(max_length=16, required=False, allow_null=True, allow_blank=True)
    photo = serializers.ImageField()

    def validate_photo(self, value):
        if value.size > settings.MAX_IMAGE_SIZE:
            raise serializers.ValidationError('图片大小超出限制')
        return value
