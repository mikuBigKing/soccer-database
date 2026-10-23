"""报名模块序列化器。"""
from rest_framework import serializers

from apps.accounts.models import AthleteProfile, CoachProfile

from .models import Competition, Registration, RegistrationLog


class CompetitionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Competition
        fields = ('id', 'name', 'year', 'status', 'status_display', 'created_at')
        read_only_fields = ('created_at',)


class RegistrationListSerializer(serializers.ModelSerializer):
    competition_name = serializers.CharField(source='competition.name', read_only=True)
    competition_year = serializers.IntegerField(source='competition.year', read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)
    group_type = serializers.CharField(source='team.group_type', read_only=True)
    group_type_display = serializers.CharField(source='team.get_group_type_display', read_only=True)
    school_name = serializers.CharField(source='team.school.name', read_only=True)
    city_id = serializers.IntegerField(source='team.school.city_id', read_only=True)
    city_name = serializers.CharField(source='team.school.city.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    reject_level_display = serializers.CharField(source='get_reject_level_display', read_only=True)

    class Meta:
        model = Registration
        fields = (
            'id', 'competition', 'competition_name', 'competition_year', 'team', 'team_name',
            'group_type', 'group_type_display', 'school_name', 'city_id', 'city_name',
            'status', 'status_display', 'reject_level', 'reject_level_display', 'reject_reason',
            'submitted_at', 'reported_at', 'created_at',
        )


class PlayerMaterialSerializer(serializers.ModelSerializer):
    """报名详情中的球员信息（含学籍材料/头像状态）。"""

    user_id = serializers.IntegerField(source='user.id', read_only=True)
    has_enrollment_cert = serializers.SerializerMethodField()
    has_avatar = serializers.SerializerMethodField()

    class Meta:
        model = AthleteProfile
        fields = (
            'id', 'user_id', 'name', 'jersey_no', 'position', 'id_card',
            'has_enrollment_cert', 'has_avatar', 'enrollment_cert_name', 'avatar_name', 'status',
        )

    def get_has_enrollment_cert(self, obj):
        return bool(obj.enrollment_cert_path)

    def get_has_avatar(self, obj):
        return bool(obj.avatar_path)


class CoachMaterialSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    has_qualification = serializers.SerializerMethodField()

    class Meta:
        model = CoachProfile
        fields = ('id', 'user_id', 'name', 'phone', 'id_card', 'has_qualification', 'qualification_name', 'status')

    def get_has_qualification(self, obj):
        return bool(obj.qualification_path)


class RegistrationDetailSerializer(RegistrationListSerializer):
    coach = serializers.SerializerMethodField()
    players = serializers.SerializerMethodField()
    has_stamped_form = serializers.SerializerMethodField()
    stamped_form_name = serializers.CharField(read_only=True)

    class Meta(RegistrationListSerializer.Meta):
        fields = RegistrationListSerializer.Meta.fields + (
            'coach', 'players', 'has_stamped_form', 'stamped_form_name',
            'city_reviewed_at', 'province_reviewed_at',
        )

    def get_coach(self, obj):
        profile = CoachProfile.objects.filter(user=obj.team.coach).first()
        return CoachMaterialSerializer(profile).data if profile else None

    def get_players(self, obj):
        athletes = AthleteProfile.objects.filter(team=obj.team).order_by('jersey_no', 'id')
        return PlayerMaterialSerializer(athletes, many=True).data

    def get_has_stamped_form(self, obj):
        return bool(obj.stamped_form_path)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class BatchReportSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=200)


class RegistrationLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    actor_name = serializers.CharField(source='actor.name', read_only=True, default='')

    class Meta:
        model = RegistrationLog
        fields = ('id', 'registration', 'action', 'action_display', 'actor', 'actor_name', 'detail', 'created_at')
