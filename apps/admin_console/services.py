"""球员 / 教练员管理业务逻辑。

约定：
- 业务拦截统一抛 BusinessException(BUSINESS_ERROR, '中文文案')；
- 响应字段名与契约文档逐字一致（avatar ← avatar_path 等）；
- 所有查询显式过滤 deleted_at__isnull=True（仓库无软删 manager）。
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import AthleteProfile, CoachProfile, ProfileStatus
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.formatting import format_dt
from apps.common.pagination import paginate
from apps.org.models import Team

from .masking import mask_id_card, mask_phone
from .models import AdminOperationLog

User = get_user_model()


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def _client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()[:64]
    return (request.META.get('REMOTE_ADDR') or '')[:64]


def _log(request, action: str, target_type: str, target_id: int, before=None, after=None) -> None:
    AdminOperationLog.objects.create(
        operator=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
        ip=_client_ip(request),
        trace_id=(getattr(request, 'trace_id', '') or '')[:64],
    )


def _ensure_phone_available(phone: str, exclude_user_id: int) -> None:
    if User.objects.filter(phone=phone).exclude(pk=exclude_user_id).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'phone: 该手机号已被使用')


# ---------------------------------------------------------------------------
# 球员
# ---------------------------------------------------------------------------

def _get_player(pk: int) -> AthleteProfile:
    try:
        return AthleteProfile.objects.select_related('user', 'team').get(pk=pk, deleted_at__isnull=True)
    except AthleteProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)


def _player_list_item(profile: AthleteProfile) -> dict:
    team = profile.team if profile.team_id else None
    return {
        'id': profile.id,
        'name': profile.name,
        'jersey_no': profile.jersey_no,
        'position': profile.position,
        'team_id': profile.team_id,
        'team_name': team.name if team else None,
        'group_type': team.group_type if team else None,
        'status': profile.status,
        'is_public': profile.is_public,
        'avatar': profile.avatar_path,
        'created_at': format_dt(profile.created_at),
    }


def _player_detail(profile: AthleteProfile) -> dict:
    data = _player_list_item(profile)
    data.update({
        'id_card': mask_id_card(profile.id_card),
        'phone': mask_phone(profile.user.phone or '') if profile.user_id else '',
        'enrollment_cert': profile.enrollment_cert_path,
        'reject_reason': profile.reject_reason,
        'updated_at': format_dt(profile.updated_at),
    })
    return data


def list_players(request, params: dict) -> dict:
    qs = (AthleteProfile.objects.filter(deleted_at__isnull=True)
          .select_related('team')
          .order_by('-created_at'))

    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    if params.get('id_card'):
        qs = qs.filter(id_card=params['id_card'])
    if 'jersey_no' in params:
        qs = qs.filter(jersey_no=params['jersey_no'])
    if 'team_id' in params:
        qs = qs.filter(team_id=params['team_id'])
    if params.get('group_type'):
        qs = qs.filter(team__group_type=params['group_type'])
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    if 'is_public' in params:
        qs = qs.filter(is_public=params['is_public'])
    if params.get('keyword'):
        keyword = params['keyword']
        qs = qs.filter(
            Q(name__icontains=keyword)
            | Q(user__phone__icontains=keyword)
            | Q(id_card__icontains=keyword)
        )

    data = paginate(qs, request)
    data['list'] = [_player_list_item(profile) for profile in data['list']]
    return data


def get_player(pk: int) -> dict:
    return _player_detail(_get_player(pk))


@transaction.atomic
def update_player(request, pk: int, data: dict) -> dict:
    player = _get_player(pk)
    before = _player_detail(player)

    changed = []
    field_map = {
        'name': 'name',
        'jersey_no': 'jersey_no',
        'position': 'position',
        'avatar': 'avatar_path',
        'id_card': 'id_card',
    }
    for source, target in field_map.items():
        if source in data:
            setattr(player, target, data[source])
            changed.append(target)

    phone_changed = False
    if 'phone' in data:
        _ensure_phone_available(data['phone'], player.user_id)
        player.user.phone = data['phone']
        phone_changed = True

    if not changed and not phone_changed:
        return before

    now = timezone.now()
    if changed:
        player.updated_at = now
        player.save(update_fields=changed + ['updated_at'])
    if phone_changed:
        player.user.save(update_fields=['phone'])

    after = _player_detail(player)
    _log(request, '修改球员', 'player', player.id, before, after)
    return after


@transaction.atomic
def delete_player(request, pk: int) -> dict:
    player = _get_player(pk)
    if player.team_id:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员已加入球队，不可删除')

    before = _player_detail(player)
    now = timezone.now()
    fields = ['deleted_at', 'updated_at']
    player.deleted_at = now
    player.updated_at = now
    if player.is_public:
        player.is_public = False
        fields.append('is_public')
    player.save(update_fields=fields)

    _log(request, '删除球员', 'player', player.id, before, None)
    return {'id': player.id, 'deleted': True}


@transaction.atomic
def publish_player(request, pk: int) -> dict:
    player = _get_player(pk)
    if player.status != ProfileStatus.APPROVED:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '仅审核通过的球员可公示')
    if player.is_public:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员已公示')

    before = _player_detail(player)
    player.is_public = True
    player.updated_at = timezone.now()
    player.save(update_fields=['is_public', 'updated_at'])

    _log(request, '公示球员', 'player', player.id, before, _player_detail(player))
    return {'id': player.id, 'is_public': True}


@transaction.atomic
def unpublish_player(request, pk: int) -> dict:
    player = _get_player(pk)
    if not player.is_public:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员未公示')

    before = _player_detail(player)
    player.is_public = False
    player.updated_at = timezone.now()
    player.save(update_fields=['is_public', 'updated_at'])

    _log(request, '取消公示球员', 'player', player.id, before, _player_detail(player))
    return {'id': player.id, 'is_public': False}


@transaction.atomic
def reset_player_password(request, pk: int) -> dict:
    player = _get_player(pk)
    player.user.set_password(settings.DEFAULT_RESET_PASSWORD)
    player.user.save(update_fields=['password'])

    _log(request, '重置球员密码', 'player', player.id, None, None)
    return {'id': player.id, 'reset': True}


# ---------------------------------------------------------------------------
# 教练员
# ---------------------------------------------------------------------------

def _get_coach(pk: int) -> CoachProfile:
    try:
        return CoachProfile.objects.select_related('user').get(pk=pk, deleted_at__isnull=True)
    except CoachProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '教练员不存在', status_code=404)


def _coach_list_item(profile: CoachProfile) -> dict:
    return {
        'id': profile.id,
        'name': profile.name,
        'phone': mask_phone(profile.phone or ''),
        'qualification': profile.qualification_path,
        'status': profile.status,
        'is_public': profile.is_public,
        'created_at': format_dt(profile.created_at),
    }


def _coach_detail(profile: CoachProfile) -> dict:
    data = _coach_list_item(profile)
    teams = Team.objects.filter(coach=profile.user).order_by('id')
    data.update({
        'id_card': mask_id_card(profile.id_card),
        'teams': [
            {'id': team.id, 'name': team.name, 'group_type': team.group_type}
            for team in teams
        ],
        'reject_reason': profile.reject_reason,
        'updated_at': format_dt(profile.updated_at),
    })
    return data


def list_coaches(request, params: dict) -> dict:
    qs = (CoachProfile.objects.filter(deleted_at__isnull=True)
          .order_by('-created_at'))

    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    if params.get('id_card'):
        qs = qs.filter(id_card=params['id_card'])
    if 'team_id' in params:
        qs = qs.filter(user__coached_teams__id=params['team_id'])
    if params.get('group_type'):
        qs = qs.filter(user__coached_teams__group_type=params['group_type'])
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    if 'is_public' in params:
        qs = qs.filter(is_public=params['is_public'])
    if params.get('keyword'):
        keyword = params['keyword']
        qs = qs.filter(
            Q(name__icontains=keyword)
            | Q(phone__icontains=keyword)
            | Q(id_card__icontains=keyword)
        )
    if 'team_id' in params or params.get('group_type'):
        qs = qs.distinct()

    data = paginate(qs, request)
    data['list'] = [_coach_list_item(profile) for profile in data['list']]
    return data


def get_coach(pk: int) -> dict:
    return _coach_detail(_get_coach(pk))


@transaction.atomic
def update_coach(request, pk: int, data: dict) -> dict:
    coach = _get_coach(pk)
    before = _coach_detail(coach)

    changed = []
    field_map = {
        'name': 'name',
        'phone': 'phone',
        'id_card': 'id_card',
        'qualification': 'qualification_path',
    }
    for source, target in field_map.items():
        if source in data:
            setattr(coach, target, data[source])
            changed.append(target)

    if not changed:
        return before

    coach.updated_at = timezone.now()
    coach.save(update_fields=changed + ['updated_at'])

    after = _coach_detail(coach)
    _log(request, '修改教练员', 'coach', coach.id, before, after)
    return after


@transaction.atomic
def delete_coach(request, pk: int) -> dict:
    coach = _get_coach(pk)
    if Team.objects.filter(coach=coach.user).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员有在执球队，不可删除')

    before = _coach_detail(coach)
    now = timezone.now()
    fields = ['deleted_at', 'updated_at']
    coach.deleted_at = now
    coach.updated_at = now
    if coach.is_public:
        coach.is_public = False
        fields.append('is_public')
    coach.save(update_fields=fields)

    _log(request, '删除教练员', 'coach', coach.id, before, None)
    return {'id': coach.id, 'deleted': True}


@transaction.atomic
def publish_coach(request, pk: int) -> dict:
    coach = _get_coach(pk)
    if coach.status != ProfileStatus.APPROVED:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '仅审核通过的教练员可公示')
    if coach.is_public:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员已公示')

    before = _coach_detail(coach)
    coach.is_public = True
    coach.updated_at = timezone.now()
    coach.save(update_fields=['is_public', 'updated_at'])

    _log(request, '公示教练员', 'coach', coach.id, before, _coach_detail(coach))
    return {'id': coach.id, 'is_public': True}


@transaction.atomic
def unpublish_coach(request, pk: int) -> dict:
    coach = _get_coach(pk)
    if not coach.is_public:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员未公示')

    before = _coach_detail(coach)
    coach.is_public = False
    coach.updated_at = timezone.now()
    coach.save(update_fields=['is_public', 'updated_at'])

    _log(request, '取消公示教练员', 'coach', coach.id, before, _coach_detail(coach))
    return {'id': coach.id, 'is_public': False}


@transaction.atomic
def reset_coach_password(request, pk: int) -> dict:
    coach = _get_coach(pk)
    coach.user.set_password(settings.DEFAULT_RESET_PASSWORD)
    coach.user.save(update_fields=['password'])

    _log(request, '重置教练员密码', 'coach', coach.id, None, None)
    return {'id': coach.id, 'reset': True}
