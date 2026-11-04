"""球员 / 教练员管理业务逻辑。

约定：
- 业务拦截统一抛 BusinessException(BUSINESS_ERROR, '中文文案')；
- 响应字段名与契约文档逐字一致（avatar ← avatar_path 等）；
- 所有查询显式过滤 deleted_at__isnull=True（仓库无软删 manager）。
"""
import io
import os
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone

from apps.accounts.models import (
    AthleteProfile, CoachProfile, EligibilityStatus, ProfileStatus, UserRole, UserStatus,
)
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.formatting import format_dt
from apps.common.pagination import paginate
from apps.live.models import Match, MatchEvent
from apps.org.models import School, Team, TeamStatus
from apps.registration.models import Registration

from .masking import mask_id_card, mask_phone
from .models import AdminOperationLog, CoachObjection, PlayerSuspension, PlayerTeamChange

User = get_user_model()

# 视为「在执 / 在队」的球队状态；rejected 视为历史球队，不阻塞删除
ACTIVE_TEAM_STATUSES = (TeamStatus.PENDING, TeamStatus.APPROVED)
# 报名进行中（草稿 / 已驳回不算），用于删除前关联校验
IN_PROGRESS_REGISTRATION_STATUSES = (
    Registration.Status.SUBMITTED,
    Registration.Status.CITY_APPROVED,
    Registration.Status.REPORTED,
    Registration.Status.APPROVED,
)


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def _client_ip(request) -> str:
    """来源 IP。

    仅当直连地址（REMOTE_ADDR）在受信代理名单内时才采信 X-Forwarded-For 首值，
    防止客户端随手伪造审计日志来源；名单缺省为空集合（不信任 XFF）。

    将来部署在 Nginx 后，在 settings 补 TRUSTED_PROXY_IPS = ['127.0.0.1', '10.0.0.5']
    即可生效（本次改动范围限定在本模块，不动 settings）。
    """
    remote_addr = (request.META.get('REMOTE_ADDR') or '')[:64]
    trusted = set(getattr(settings, 'TRUSTED_PROXY_IPS', []) or [])
    if remote_addr in trusted:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            candidate = forwarded.split(',')[0].strip()
            if candidate:
                return candidate[:64]
    return remote_addr


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


def _sync_user_phone(user, new_phone: str) -> bool:
    """同步账号手机号与登录标识（注册约定 `username = phone`）。

    返回是否发生变化。手机号 / username 均查重并排除自己：
    - 手机号冲突：`phone: 该手机号已被使用`
    - username 冲突：`username: 该手机号已被占用`
    """
    if user.phone == new_phone and user.username == new_phone:
        return False
    if User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'phone: 该手机号已被使用')
    if User.objects.filter(username=new_phone).exclude(pk=user.pk).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'username: 该手机号已被占用')
    user.phone = new_phone
    user.username = new_phone
    return True


def _ensure_id_card_available(id_card: str, profile_model, exclude_pk: int) -> None:
    """同类型档案内校验身份证唯一（应用层校验，暂不加 DB 约束）。

    历史数据可能已有重复，加 unique 会导致迁移失败，故只在应用层拦截；
    已软删的记录不参与占用判断。
    """
    if not id_card:
        return
    exists = (profile_model.objects
              .filter(id_card=id_card, deleted_at__isnull=True)
              .exclude(pk=exclude_pk)
              .exists())
    if exists:
        raise BusinessException(ResponseCode.PARAM_INVALID, 'id_card: 该身份证号已被使用')


def _disable_user_account(user) -> None:
    """软删档案的同时禁用登录账号，避免「已删除的人还能登录」。"""
    if user is None:
        return
    fields = []
    if user.status != UserStatus.DISABLED:
        user.status = UserStatus.DISABLED
        fields.append('status')
    if user.is_active:
        user.is_active = False
        fields.append('is_active')
    if fields:
        user.save(update_fields=fields)


def _paginate(qs, request) -> dict:
    """分页包装：把越界回显的 page 修正为实际返回内容所在页。

    公共 `paginate()` 在页码越界时内容已回退到最后一页，但 `page` 仍回显请求值，
    前端会误以为在看第 99 页。这里按 total/page_size 反算真实页码后覆盖，
    不动 `apps/common/pagination.py`。page_size 用公共分页夹过的值（1–100）。
    """
    data = paginate(qs, request)
    page_size = data['page_size'] or 1
    total_pages = max(1, (data['total'] + page_size - 1) // page_size)
    data['page'] = min(data['page'], total_pages)
    return data


def _is_public_effective(is_public: bool, end_at) -> bool:
    """公示是否「此刻生效」。

    过期公示不靠定时任务，读取时惰性判断（is_public=True 但已过期 → 视为未公示）。
    """
    if not is_public:
        return False
    return end_at is None or end_at > timezone.now()


def _school_of(profile):
    """学校归属：档案直挂 school 优先，否则回落到球队学校。"""
    if profile.school_id:
        return profile.school
    if profile.team_id and profile.team:
        return profile.team.school
    return None


def _enable_user_account(user) -> None:
    fields = []
    if user.status != UserStatus.ACTIVE:
        user.status = UserStatus.ACTIVE
        fields.append('status')
    if not user.is_active:
        user.is_active = True
        fields.append('is_active')
    if fields:
        user.save(update_fields=fields)


def _ensure_phone_available(phone: str, exclude_user_id=None) -> None:
    """手机号 / 登录名查重（新增球员用；修改走 `_sync_user_phone`）。"""
    qs = User.objects.filter(phone=phone)
    if exclude_user_id:
        qs = qs.exclude(pk=exclude_user_id)
    if qs.exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'phone: 该手机号已被使用')
    if User.objects.filter(username=phone).exclude(pk=exclude_user_id or 0).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, 'username: 该手机号已被占用')


def _get_school(school_id):
    school = School.objects.filter(pk=school_id).first()
    if school is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, 'school_id: 学校不存在')
    return school


# ---------------------------------------------------------------------------
# 球员
# ---------------------------------------------------------------------------

def _get_player(pk: int, for_update: bool = False, with_deleted: bool = False, request=None) -> AthleteProfile:
    qs = AthleteProfile.objects.select_related('user', 'team__school', 'school')
    if for_update:
        # 公示类操作的「查 → 判断 → 改」需要行锁，避免并发重复公示
        qs = qs.select_for_update()
    try:
        if with_deleted:
            profile = qs.get(pk=pk)
        else:
            profile = qs.get(pk=pk, deleted_at__isnull=True)
    except AthleteProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)
    if request is not None and getattr(request, 'user', None) is not None:
        _ensure_in_scope(request.user, profile, kind='player')
    return profile


def _player_list_item(profile: AthleteProfile) -> dict:
    team = profile.team if profile.team_id else None
    school = _school_of(profile)
    return {
        'id': profile.id,
        'name': profile.name,
        'gender': profile.gender,
        'jersey_no': profile.jersey_no,
        'position': profile.position,
        'team_id': profile.team_id,
        'team_name': team.name if team else None,
        'group_type': team.group_type if team else None,
        'school_id': school.id if school else None,
        'school_name': school.name if school else None,
        'grade': profile.grade,
        'height_cm': profile.height_cm,
        'weight_kg': profile.weight_kg,
        'status': profile.status,
        'is_public': profile.is_public,
        'real_name_status': profile.real_name_status,
        'enrollment_status': profile.enrollment_status,
        'eligibility_status': profile.eligibility_status,
        'avatar': profile.avatar_path,
        'created_at': format_dt(profile.created_at),
        'deleted_at': format_dt(profile.deleted_at),
    }


def _player_detail(profile: AthleteProfile) -> dict:
    data = _player_list_item(profile)
    latest = profile.suspensions.order_by('-id').first()
    data.update({
        'id_card': mask_id_card(profile.id_card),
        'phone': mask_phone(profile.user.phone or '') if profile.user_id else '',
        'enrollment_cert': profile.enrollment_cert_path,
        'reject_reason': profile.reject_reason,
        'suspensions': {
            'active_count': profile.suspensions.filter(
                status=PlayerSuspension.Status.ACTIVE,
            ).count(),
            'latest': _suspension_item(latest) if latest else None,
        },
        'updated_at': format_dt(profile.updated_at),
    })
    return data


def list_players(request, params: dict) -> dict:
    qs = (AthleteProfile.objects
          .select_related('team__school', 'school')
          .order_by('-created_at'))
    qs = apply_scope(qs, request.user, kind='player')
    if params.get('deleted'):
        qs = qs.filter(deleted_at__isnull=False)
    else:
        qs = qs.filter(deleted_at__isnull=True)

    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    if params.get('id_card'):
        qs = qs.filter(id_card__iexact=params['id_card'])
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

    data = _paginate(qs, request)
    data['list'] = [_player_list_item(profile) for profile in data['list']]
    return data


def get_player(request, pk: int) -> dict:
    return _player_detail(_get_player(pk, request=request))


@transaction.atomic
def create_player(request, data: dict) -> dict:
    phone = data['phone']
    id_card = data['id_card']
    _ensure_phone_available(phone)
    _ensure_id_card_available(id_card, AthleteProfile, None)

    school = _get_school(data['school_id']) if data.get('school_id') else None
    _ensure_school_in_scope(request.user, school)
    user = User.objects.create_user(
        username=phone,
        phone=phone,
        role=UserRole.ATHLETE,
        name=data['name'],
        password=settings.DEFAULT_RESET_PASSWORD,
    )
    profile = AthleteProfile.objects.create(
        user=user,
        name=data['name'],
        id_card=id_card,
        school=school,
        gender=data.get('gender'),
        grade=data.get('grade') or None,
        position=data.get('position') or None,
        jersey_no=data.get('jersey_no'),
        height_cm=data.get('height_cm'),
        weight_kg=data.get('weight_kg'),
        status=ProfileStatus.PENDING,
    )
    # 新建档案默认待审核，后续走审核 / 状态标记接口

    after = _player_detail(profile)
    _log(request, '新增球员', 'player', profile.id, None, after)
    return after


@transaction.atomic
def update_player(request, pk: int, data: dict) -> dict:
    player = _get_player(pk, request=request)
    before = _player_detail(player)

    changed = []
    field_map = {
        'name': 'name',
        'jersey_no': 'jersey_no',
        'position': 'position',
        'avatar': 'avatar_path',
        'id_card': 'id_card',
        'gender': 'gender',
        'grade': 'grade',
        'height_cm': 'height_cm',
        'weight_kg': 'weight_kg',
    }
    if data.get('id_card'):
        _ensure_id_card_available(data['id_card'], AthleteProfile, player.pk)
    if 'school_id' in data:
        school_id = data['school_id']
        school = _get_school(school_id) if school_id else None
        _ensure_school_in_scope(request.user, school, action='transfer')
        player.school = school
        changed.append('school')
    for source, target in field_map.items():
        if source in data:
            setattr(player, target, data[source])
            changed.append(target)

    phone_changed = False
    if 'phone' in data:
        phone_changed = _sync_user_phone(player.user, data['phone'])

    if not changed and not phone_changed:
        return before

    now = timezone.now()
    if changed:
        player.updated_at = now
        player.save(update_fields=changed + ['updated_at'])
    if phone_changed:
        player.user.save(update_fields=['phone', 'username'])

    after = _player_detail(player)
    _log(request, '修改球员', 'player', player.id, before, after)
    return after


@transaction.atomic
def update_player_status_flags(request, pk: int, data: dict) -> dict:
    """状态标记（实名 / 学籍 / 参赛资格）。

    说明：eligibility_status 改 suspended 时不生成停赛记录（停赛记录在阶段二打通）。
    """
    player = _get_player(pk, request=request)
    before = _player_detail(player)

    changed = []
    for field in ('real_name_status', 'enrollment_status', 'eligibility_status'):
        if field in data:
            setattr(player, field, data[field])
            changed.append(field)
    if not changed:
        return before

    player.updated_at = timezone.now()
    player.save(update_fields=changed + ['updated_at'])

    after = _player_detail(player)
    reason = (data.get('reason') or '').strip()
    if reason:
        after['reason'] = reason  # 留痕：取消资格等必须写原因
    _log(request, '修改球员状态', 'player', player.id, before, after)
    return after


@transaction.atomic
def delete_player(request, pk: int) -> dict:
    player = _get_player(pk, for_update=True, request=request)
    # 只拦「待审核 / 已通过」的球队；球队已驳回时球员可删除
    if player.team_id and player.team.status in ACTIVE_TEAM_STATUSES:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员已加入球队，不可删除')
    if player.team_id and Registration.objects.filter(
        team=player.team, status__in=IN_PROGRESS_REGISTRATION_STATUSES,
    ).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '球员所在球队有进行中的报名，不可删除')
    if MatchEvent.objects.filter(athlete=player).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '球员有赛事记录，不可删除')

    before = _player_detail(player)
    now = timezone.now()
    fields = ['deleted_at', 'updated_at']
    player.deleted_at = now
    player.updated_at = now
    if player.is_public:
        player.is_public = False
        fields.append('is_public')
    player.save(update_fields=fields)

    # 软删档案的同时禁用账号，避免「已删除的人仍能登录」
    _disable_user_account(player.user)

    _log(request, '删除球员', 'player', player.id, before, None)
    return {'id': player.id, 'deleted': True}


@transaction.atomic
def publish_player(request, pk: int) -> dict:
    player = _get_player(pk, for_update=True, request=request)
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
    player = _get_player(pk, for_update=True, request=request)
    if not player.is_public:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员未公示')

    before = _player_detail(player)
    player.is_public = False
    player.updated_at = timezone.now()
    player.save(update_fields=['is_public', 'updated_at'])

    _log(request, '取消公示球员', 'player', player.id, before, _player_detail(player))
    return {'id': player.id, 'is_public': False}


def _reset_password_value(data) -> str:
    """自定义密码走序列化器策略校验；未传则用默认密码。"""
    return (data or {}).get('password') or settings.DEFAULT_RESET_PASSWORD


@transaction.atomic
def reset_player_password(request, pk: int, data=None) -> dict:
    player = _get_player(pk, request=request)
    password = _reset_password_value(data)
    player.user.set_password(password)
    player.user.save(update_fields=['password'])

    _log(request, '重置球员密码', 'player', player.id, None, None)
    return {'id': player.id, 'reset': True}


@transaction.atomic
def batch_reset_player_password(request, data: dict) -> dict:
    """批量重置：逐条独立处理，已删除 / 不存在跳过；整体只写一条审计日志。"""
    ids = data['ids']
    custom_password = bool(data.get('password'))
    password = _reset_password_value(data)
    success = 0
    skipped = []
    for player_id in ids:
        player = (AthleteProfile.objects.filter(pk=player_id, deleted_at__isnull=True)
                  .select_related('user').first())
        if player is None:
            skipped.append({'id': player_id, 'reason': '球员不存在或已删除'})
            continue
        player.user.set_password(password)
        player.user.save(update_fields=['password'])
        success += 1

    result = {'total': len(ids), 'success': success, 'skipped': skipped}
    if not custom_password:
        # 仅默认密码回显，自定义密码不回显
        result['password'] = settings.DEFAULT_RESET_PASSWORD
    _log(request, '批量重置球员密码', 'player', 0, {'ids': ids}, result)
    return result


@transaction.atomic
def restore_player(request, pk: int) -> dict:
    player = _get_player(pk, with_deleted=True, request=request)
    if player.deleted_at is None:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员未在归档中')

    phone = player.user.phone
    if phone and User.objects.filter(phone=phone).exclude(pk=player.user_id).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '原手机号已被其他账号占用，无法恢复')
    if player.id_card and AthleteProfile.objects.filter(
        id_card=player.id_card, deleted_at__isnull=True,
    ).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '原身份证号已被其他在册球员占用，无法恢复')

    before = _player_detail(player)
    player.deleted_at = None
    player.updated_at = timezone.now()
    player.save(update_fields=['deleted_at', 'updated_at'])
    _enable_user_account(player.user)

    after = _player_detail(player)
    _log(request, '恢复球员', 'player', player.id, before, after)
    return after


@transaction.atomic
def update_player_account_status(request, pk: int, data: dict) -> dict:
    player = _get_player(pk, request=request)
    user = player.user
    before = {'status': user.status, 'is_active': user.is_active}

    if data['action'] == 'enable':
        _enable_user_account(user)
    else:
        _disable_user_account(user)

    after = {'status': user.status, 'is_active': user.is_active}
    _log(request, '修改球员账号状态', 'player', player.id, before, after)
    return {'id': player.id, **after}


# ---------------------------------------------------------------------------
# 教练员
# ---------------------------------------------------------------------------

def _get_coach(pk: int, for_update: bool = False, with_deleted: bool = False, request=None) -> CoachProfile:
    qs = CoachProfile.objects.select_related('user')
    if for_update:
        # 公示类操作的「查 → 判断 → 改」需要行锁，避免并发重复公示
        qs = qs.select_for_update()
    try:
        if with_deleted:
            profile = qs.get(pk=pk)
        else:
            profile = qs.get(pk=pk, deleted_at__isnull=True)
    except CoachProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '教练员不存在', status_code=404)
    if request is not None and getattr(request, 'user', None) is not None:
        _ensure_in_scope(request.user, profile, kind='coach')
    return profile


def _coach_list_item(profile: CoachProfile) -> dict:
    return {
        'id': profile.id,
        'name': profile.name,
        'phone': mask_phone(profile.phone or ''),
        'qualification': profile.qualification_path,
        'status': profile.status,
        # 过期公示惰性判定为未公示
        'is_public': _is_public_effective(profile.is_public, profile.public_end_at),
        'created_at': format_dt(profile.created_at),
        'deleted_at': format_dt(profile.deleted_at),
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
        'public_start_at': format_dt(profile.public_start_at),
        'public_end_at': format_dt(profile.public_end_at),
        'unpublish_reason': profile.unpublish_reason,
        'reject_reason': profile.reject_reason,
        'updated_at': format_dt(profile.updated_at),
    })
    return data


def list_coaches(request, params: dict) -> dict:
    qs = CoachProfile.objects.prefetch_related('user__coached_teams__school').order_by('-created_at')
    qs = apply_scope(qs, request.user, kind='coach')
    if params.get('deleted'):
        qs = qs.filter(deleted_at__isnull=False)
    else:
        qs = qs.filter(deleted_at__isnull=True)

    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    if params.get('id_card'):
        qs = qs.filter(id_card__iexact=params['id_card'])
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

    data = _paginate(qs, request)
    data['list'] = [_coach_list_item(profile) for profile in data['list']]
    return data


def get_coach(request, pk: int) -> dict:
    return _coach_detail(_get_coach(pk, request=request))


@transaction.atomic
def update_coach(request, pk: int, data: dict) -> dict:
    coach = _get_coach(pk, request=request)
    before = _coach_detail(coach)

    changed = []
    field_map = {
        'name': 'name',
        'phone': 'phone',
        'id_card': 'id_card',
        'qualification': 'qualification_path',
    }
    if data.get('id_card'):
        _ensure_id_card_available(data['id_card'], CoachProfile, coach.pk)
    for source, target in field_map.items():
        if source in data:
            setattr(coach, target, data[source])
            changed.append(target)

    phone_changed = False
    if 'phone' in data:
        # 登录按 User.phone 查账号，档案手机号必须与账号同步
        phone_changed = _sync_user_phone(coach.user, data['phone'])

    if not changed:
        return before

    coach.updated_at = timezone.now()
    coach.save(update_fields=changed + ['updated_at'])
    if phone_changed:
        coach.user.save(update_fields=['phone', 'username'])

    after = _coach_detail(coach)
    _log(request, '修改教练员', 'coach', coach.id, before, after)
    return after


@transaction.atomic
def delete_coach(request, pk: int) -> dict:
    coach = _get_coach(pk, for_update=True, request=request)
    # 只拦「待审核 / 已通过」的球队；球队已驳回时教练可删除
    active_teams = Team.objects.filter(coach=coach.user, status__in=ACTIVE_TEAM_STATUSES)
    if active_teams.exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员有在执球队，不可删除')
    coach_teams = Team.objects.filter(coach=coach.user)
    if Registration.objects.filter(
        team__in=coach_teams, status__in=IN_PROGRESS_REGISTRATION_STATUSES,
    ).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '教练所在球队有进行中的报名，不可删除')
    if Match.objects.filter(
        Q(home_team__in=coach_teams) | Q(away_team__in=coach_teams),
    ).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '教练有赛事记录，不可删除')

    before = _coach_detail(coach)
    now = timezone.now()
    fields = ['deleted_at', 'updated_at']
    coach.deleted_at = now
    coach.updated_at = now
    if coach.is_public:
        coach.is_public = False
        fields.append('is_public')
    coach.save(update_fields=fields)

    # 软删档案的同时禁用账号，避免「已删除的人仍能登录」
    _disable_user_account(coach.user)

    _log(request, '删除教练员', 'coach', coach.id, before, None)
    return {'id': coach.id, 'deleted': True}


@transaction.atomic
def publish_coach(request, pk: int, data=None) -> dict:
    coach = _get_coach(pk, for_update=True, request=request)
    if coach.status != ProfileStatus.APPROVED:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '仅审核通过的教练员可公示')
    # 已过期公示视为未公示，允许重新设置公示期
    if _is_public_effective(coach.is_public, coach.public_end_at):
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员已公示')

    before = _coach_detail(coach)
    coach.is_public = True
    coach.public_start_at = (data or {}).get('start_at')
    coach.public_end_at = (data or {}).get('end_at')
    coach.unpublish_reason = None
    coach.updated_at = timezone.now()
    coach.save(update_fields=[
        'is_public', 'public_start_at', 'public_end_at', 'unpublish_reason', 'updated_at',
    ])

    _log(request, '公示教练员', 'coach', coach.id, before, _coach_detail(coach))
    return {'id': coach.id, 'is_public': True}


@transaction.atomic
def unpublish_coach(request, pk: int, data: dict) -> dict:
    coach = _get_coach(pk, for_update=True, request=request)
    if not _is_public_effective(coach.is_public, coach.public_end_at):
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员未公示')
    reason = data['reason'].strip()

    before = _coach_detail(coach)
    coach.is_public = False
    coach.unpublish_reason = reason
    coach.updated_at = timezone.now()
    coach.save(update_fields=['is_public', 'unpublish_reason', 'updated_at'])

    after = _coach_detail(coach)
    after['reason'] = reason  # 取消公示原因留痕
    _log(request, '取消公示教练员', 'coach', coach.id, before, after)
    return {'id': coach.id, 'is_public': False}


@transaction.atomic
def reset_coach_password(request, pk: int, data=None) -> dict:
    coach = _get_coach(pk, request=request)
    password = _reset_password_value(data)
    coach.user.set_password(password)
    coach.user.save(update_fields=['password'])

    _log(request, '重置教练员密码', 'coach', coach.id, None, None)
    return {'id': coach.id, 'reset': True}


@transaction.atomic
def batch_reset_coach_password(request, data: dict) -> dict:
    """批量重置：逐条独立处理，已删除 / 不存在跳过；整体只写一条审计日志。"""
    ids = data['ids']
    custom_password = bool(data.get('password'))
    password = _reset_password_value(data)
    success = 0
    skipped = []
    for coach_id in ids:
        coach = (CoachProfile.objects.filter(pk=coach_id, deleted_at__isnull=True)
                 .select_related('user').first())
        if coach is None:
            skipped.append({'id': coach_id, 'reason': '教练员不存在或已删除'})
            continue
        coach.user.set_password(password)
        coach.user.save(update_fields=['password'])
        success += 1

    result = {'total': len(ids), 'success': success, 'skipped': skipped}
    if not custom_password:
        result['password'] = settings.DEFAULT_RESET_PASSWORD
    _log(request, '批量重置教练员密码', 'coach', 0, {'ids': ids}, result)
    return result


@transaction.atomic
def restore_coach(request, pk: int) -> dict:
    coach = _get_coach(pk, with_deleted=True, request=request)
    if coach.deleted_at is None:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该教练员未在归档中')

    phone = coach.user.phone
    if phone and User.objects.filter(phone=phone).exclude(pk=coach.user_id).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '原手机号已被其他账号占用，无法恢复')
    if coach.id_card and CoachProfile.objects.filter(
        id_card=coach.id_card, deleted_at__isnull=True,
    ).exists():
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '原身份证号已被其他在册教练占用，无法恢复')

    before = _coach_detail(coach)
    coach.deleted_at = None
    coach.updated_at = timezone.now()
    coach.save(update_fields=['deleted_at', 'updated_at'])
    _enable_user_account(coach.user)

    after = _coach_detail(coach)
    _log(request, '恢复教练员', 'coach', coach.id, before, after)
    return after


@transaction.atomic
def update_coach_account_status(request, pk: int, data: dict) -> dict:
    coach = _get_coach(pk, request=request)
    user = coach.user
    before = {'status': user.status, 'is_active': user.is_active}

    if data['action'] == 'enable':
        _enable_user_account(user)
    else:
        _disable_user_account(user)

    after = {'status': user.status, 'is_active': user.is_active}
    _log(request, '修改教练员账号状态', 'coach', coach.id, before, after)
    return {'id': coach.id, **after}


# ---------------------------------------------------------------------------
# 公示期异议
# ---------------------------------------------------------------------------

def _objection_item(obj: CoachObjection) -> dict:
    return {
        'id': obj.id,
        'coach_id': obj.coach_id,
        'submitter_name': obj.submitter_name,
        'contact': obj.contact,
        'content': obj.content,
        'handled': obj.handled,
        'created_at': format_dt(obj.created_at),
    }


@transaction.atomic
def create_objection(request, coach_id: int, data: dict) -> dict:
    coach = _get_coach(coach_id, request=request)
    obj = CoachObjection.objects.create(coach=coach, **data)
    _log(request, '代录公示异议', 'coach', coach.id, None, _objection_item(obj))
    return _objection_item(obj)


def list_objections(request, coach_id: int, params: dict) -> dict:
    coach = _get_coach(coach_id, request=request)
    qs = CoachObjection.objects.filter(coach=coach).order_by('-id')
    if 'handled' in params:
        qs = qs.filter(handled=params['handled'])
    data = _paginate(qs, request)
    data['list'] = [_objection_item(obj) for obj in data['list']]
    return data


@transaction.atomic
def handle_objection(request, pk: int, data: dict) -> dict:
    obj = CoachObjection.objects.select_related('coach').filter(pk=pk).first()
    if obj is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '异议不存在', status_code=404)
    if obj.handled:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该异议已处理')

    before = _objection_item(obj)
    obj.handled = True
    obj.save(update_fields=['handled'])

    after = _objection_item(obj)
    note = (data.get('note') or '').strip()
    if note:
        after['note'] = note
    _log(request, '处理公示异议', 'coach', obj.coach_id, before, after)
    return after


# ---------------------------------------------------------------------------
# 停赛联动（阶段二）
# ---------------------------------------------------------------------------

def _suspension_rules() -> dict:
    rules = getattr(settings, 'SUSPENSION_RULES', {}) or {}
    return {
        'yellow_threshold': int(rules.get('yellow_threshold', 4)),
        'yellow_ban_matches': int(rules.get('yellow_ban_matches', 1)),
        'red_ban_matches': int(rules.get('red_ban_matches', 2)),
    }


def _suspension_item(record: PlayerSuspension) -> dict:
    return {
        'id': record.id,
        'player_id': record.player_id,
        'reason': record.reason,
        'matches_count': record.matches_count,
        'start_at': format_dt(record.start_at),
        'end_at': format_dt(record.end_at),
        'source': record.source,
        'source_events': record.source_events or [],
        'status': record.status,
        'created_by': record.created_by,
        'created_at': format_dt(record.created_at),
    }


def _suspend_player_if_needed(player, request):
    """若球员当前未被取消资格，则置为停赛中并把原状态写入 prev_eligibility。

    返回 prev_eligibility 值；取消资格的球员返回 None（不停赛记录原状态）。
    """
    if player.eligibility_status == EligibilityStatus.DISQUALIFIED:
        return None
    prev = player.eligibility_status
    player.eligibility_status = EligibilityStatus.SUSPENDED
    player.updated_at = timezone.now()
    player.save(update_fields=['eligibility_status', 'updated_at'])
    return prev


def _card_events(player: AthleteProfile) -> list:
    return list(
        MatchEvent.objects
        .filter(athlete=player, type__in=('yellow', 'red'))
        .order_by('created_at', 'id')
    )


def _consumed_event_ids(player: AthleteProfile) -> set:
    """已被任意停赛记录计入过的红黄牌事件 id（含已撤销 / 已结束记录，保证幂等）。"""
    consumed = set()
    for record in PlayerSuspension.objects.filter(player=player):
        for event_id in record.source_events or []:
            consumed.add(int(event_id))
    return consumed


def suspension_preview(request, pk: int) -> dict:
    """停赛提示：纯计算不落库。"""
    player = _get_player(pk, request=request)
    events = _card_events(player)
    consumed = _consumed_event_ids(player)
    rule = _suspension_rules()
    yellow_events = [event for event in events if event.type == 'yellow']
    red_events = [event for event in events if event.type == 'red']
    yellow_since = len([event for event in yellow_events if event.id not in consumed])
    red_since = len([event for event in red_events if event.id not in consumed])
    return {
        'yellow_total': len(yellow_events),
        'red_total': len(red_events),
        'yellow_since_last_ban': yellow_since,
        'will_suspend': red_since > 0 or yellow_since >= rule['yellow_threshold'],
        'rule': rule,
    }


def list_suspensions(request, pk: int, params: dict) -> dict:
    player = _get_player(pk, request=request)
    qs = PlayerSuspension.objects.filter(player=player).order_by('-id')
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    data = _paginate(qs, request)
    data['list'] = [_suspension_item(record) for record in data['list']]
    return data


def _create_suspension(player, reason, matches_count, source, events, start_at, request, prev_eligibility=None):
    return PlayerSuspension.objects.create(
        player=player,
        reason=reason,
        matches_count=matches_count,
        start_at=start_at,
        # 场次由赛事方执行，不设固定结束时间
        end_at=None,
        source=source,
        source_events=events,
        prev_eligibility=prev_eligibility,
        status=PlayerSuspension.Status.ACTIVE,
        created_by=request.user.id,
    )


@transaction.atomic
def generate_suspension(request, pk: int) -> dict:
    """按红黄牌累计自动生成停赛记录（幂等：已计入的事件不重复）。"""
    player = _get_player(pk, for_update=True, request=request)
    events = _card_events(player)
    consumed = _consumed_event_ids(player)
    rule = _suspension_rules()
    new_yellow = [event for event in events if event.type == 'yellow' and event.id not in consumed]
    new_red = [event for event in events if event.type == 'red' and event.id not in consumed]

    created = []
    now = timezone.now()
    prev = _suspend_player_if_needed(player, request)
    if new_red:
        record = _create_suspension(
            player, f'红牌停赛（{len(new_red)} 张红牌）', rule['red_ban_matches'],
            PlayerSuspension.Source.AUTO, [event.id for event in new_red], now, request,
            prev_eligibility=prev,
        )
        created.append(_suspension_item(record))
    if len(new_yellow) >= rule['yellow_threshold']:
        record = _create_suspension(
            player, f'累计 {len(new_yellow)} 张黄牌停赛', rule['yellow_ban_matches'],
            PlayerSuspension.Source.AUTO, [event.id for event in new_yellow], now, request,
            prev_eligibility=prev,
        )
        created.append(_suspension_item(record))

    if not created:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '未达到停赛条件')

    _log(request, '自动生成停赛记录', 'player', player.id, None, {'created': created})
    return {'created': created}


@transaction.atomic
def create_manual_suspension(request, pk: int, data: dict) -> dict:
    player = _get_player(pk, for_update=True, request=request)
    now = timezone.now()
    # 取消资格的球员不覆盖状态；否则仅首条生效停赛记录原状态
    if player.eligibility_status == EligibilityStatus.DISQUALIFIED:
        prev = None
    else:
        has_active = PlayerSuspension.objects.filter(
            player=player, status=PlayerSuspension.Status.ACTIVE,
        ).exists()
        if has_active:
            prev = None
            if player.eligibility_status != EligibilityStatus.SUSPENDED:
                player.eligibility_status = EligibilityStatus.SUSPENDED
                player.updated_at = now
                player.save(update_fields=['eligibility_status', 'updated_at'])
        else:
            prev = _suspend_player_if_needed(player, request)
    record = _create_suspension(
        player, data['reason'].strip(), data['matches_count'], PlayerSuspension.Source.MANUAL,
        [], data.get('start_at') or now, request, prev_eligibility=prev,
    )

    item = _suspension_item(record)
    _log(request, '人工停赛', 'player', player.id, None, item)
    return item


@transaction.atomic
def revoke_suspension(request, pk: int, data: dict) -> dict:
    record = (PlayerSuspension.objects
              .select_related('player')
              .filter(pk=pk)
              .first())
    if record is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '停赛记录不存在', status_code=404)
    if record.status != PlayerSuspension.Status.ACTIVE:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该停赛记录已结束')

    before = _suspension_item(record)
    record.status = PlayerSuspension.Status.REVOKED
    record.save(update_fields=['status'])

    player = record.player
    remaining = PlayerSuspension.objects.filter(
        player=player, status=PlayerSuspension.Status.ACTIVE,
    ).exists()
    if not remaining:
        # 恢复为该球员最近一次已撤销记录的 prev_eligibility（为空回退 eligible）
        restore_to = (
            PlayerSuspension.objects
            .filter(player=player, status=PlayerSuspension.Status.REVOKED)
            .exclude(prev_eligibility__isnull=True)
            .order_by('-id')
            .values_list('prev_eligibility', flat=True)
            .first()
        )
        if restore_to is None:
            # 历史数据兼容：从未记录过 prev_eligibility 的旧数据
            restore_to = (
                EligibilityStatus.DISQUALIFIED
                if player.eligibility_status == EligibilityStatus.DISQUALIFIED
                else EligibilityStatus.ELIGIBLE
            )
        if player.eligibility_status != restore_to:
            player.eligibility_status = restore_to
            player.updated_at = timezone.now()
            player.save(update_fields=['eligibility_status', 'updated_at'])

    after = _suspension_item(record)
    after['reason'] = data['reason'].strip()  # 撤销原因留痕
    _log(request, '撤销停赛记录', 'player', player.id, before, after)
    return after


# ---------------------------------------------------------------------------
# 球队归属变更（阶段二）
# ---------------------------------------------------------------------------

def _team_change_item(change: PlayerTeamChange) -> dict:
    return {
        'id': change.id,
        'player_id': change.player_id,
        'from_team_id': change.from_team_id,
        'from_team_name': change.from_team.name if change.from_team_id and change.from_team else None,
        'to_team_id': change.to_team_id,
        'to_team_name': change.to_team.name if change.to_team_id and change.to_team else None,
        'reason': change.reason,
        'changed_by': change.changed_by,
        'created_at': format_dt(change.created_at),
    }


def _active_team(team_id):
    if team_id is None:
        return None
    team = Team.objects.filter(pk=team_id).first()
    if team is None or team.status not in ACTIVE_TEAM_STATUSES:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '仅可绑定待审核或已通过的球队')
    return team


@transaction.atomic
def change_player_team(request, pk: int, data: dict) -> dict:
    player = _get_player(pk, for_update=True, request=request)
    from_team = player.team if player.team_id else None
    to_team = _active_team(data['team_id'])
    _ensure_team_in_scope(request.user, to_team)
    reason = data['reason'].strip()

    # 噪音变更拦截：换同队、空队解绑
    if (from_team.id if from_team else None) == (to_team.id if to_team else None):
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该球员已在该球队中')

    change = PlayerTeamChange.objects.create(
        player=player,
        from_team=from_team,
        to_team=to_team,
        reason=reason,
        changed_by=request.user.id,
    )
    player.team = to_team
    player.updated_at = timezone.now()
    player.save(update_fields=['team', 'updated_at'])

    before = {
        'team_id': from_team.id if from_team else None,
        'team_name': from_team.name if from_team else None,
    }
    after = {
        'team_id': to_team.id if to_team else None,
        'team_name': to_team.name if to_team else None,
        'reason': reason,
    }
    _log(request, '球员归属变更', 'player', player.id, before, after)
    return _team_change_item(change)


def list_team_changes(request, pk: int, params: dict) -> dict:
    player = _get_player(pk, request=request)
    qs = (PlayerTeamChange.objects
          .filter(player=player)
          .select_related('from_team', 'to_team')
          .order_by('-id'))
    data = _paginate(qs, request)
    data['list'] = [_team_change_item(change) for change in data['list']]
    return data


# ---------------------------------------------------------------------------
# 合并重复档案（阶段二）
# ---------------------------------------------------------------------------

# 主档案缺失时从重复档案补齐的字段（"基本信息"类）
MERGE_FILL_FIELDS = (
    'school_id', 'gender', 'grade', 'height_cm', 'weight_kg',
    'avatar_path', 'position', 'jersey_no', 'id_card',
)


def _fill_missing_fields(primary, duplicate):
    filled = []
    for field in MERGE_FILL_FIELDS:
        if getattr(primary, field) in (None, '') and getattr(duplicate, field) not in (None, ''):
            setattr(primary, field, getattr(duplicate, field))
            filled.append(field)
    return filled


def _in_active_team(player: AthleteProfile) -> bool:
    return bool(player.team_id) and player.team.status in ACTIVE_TEAM_STATUSES


@transaction.atomic
def merge_players(request, data: dict) -> dict:
    primary = _get_player(data['primary_id'], for_update=True, request=request)
    duplicate = _get_player(data['duplicate_id'], for_update=True, request=request)

    if primary.id == duplicate.id:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '不能合并同一条档案')
    same_name = primary.name == duplicate.name
    same_id_card = bool(primary.id_card) and primary.id_card == duplicate.id_card
    if not same_name and not same_id_card:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '两人姓名或身份证号需至少一项相同')
    if _in_active_team(primary) and _in_active_team(duplicate):
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '两人均在有效球队中，请先调整')

    reason = data['reason'].strip()
    before = _player_detail(duplicate)

    # 关联数据统一迁移到主档案
    MatchEvent.objects.filter(athlete=duplicate).update(athlete=primary)
    PlayerTeamChange.objects.filter(player=duplicate).update(player=primary)
    PlayerSuspension.objects.filter(player=duplicate).update(player=primary)

    filled = _fill_missing_fields(primary, duplicate)
    # 球队归属继承：主档案无队、重复档案有队且球队处于有效状态时才继承并写变更记录
    if (
        not primary.team_id and duplicate.team_id
        and duplicate.team.status in ACTIVE_TEAM_STATUSES
    ):
        inherited = duplicate.team
        PlayerTeamChange.objects.create(
            player=primary,
            from_team=None,
            to_team=inherited,
            reason='合并档案继承',
            changed_by=request.user.id,
        )
        primary.team = inherited
        filled.append('team_id')
    # 停赛资格同步：迁移后若主档案存在生效停赛且当前不是 suspended/disqualified，置为 suspended
    has_active_suspension = PlayerSuspension.objects.filter(
        player=primary, status=PlayerSuspension.Status.ACTIVE,
    ).exists()
    if has_active_suspension:
        if primary.eligibility_status not in (
            EligibilityStatus.SUSPENDED, EligibilityStatus.DISQUALIFIED,
        ):
            primary.eligibility_status = EligibilityStatus.SUSPENDED
            filled.append('eligibility_status')
    if filled:
        primary.updated_at = timezone.now()
        primary.save(update_fields=list(set(filled)) + ['updated_at'])

    now = timezone.now()
    fields = ['deleted_at', 'updated_at']
    duplicate.deleted_at = now
    duplicate.updated_at = now
    if duplicate.is_public:
        duplicate.is_public = False
        fields.append('is_public')
    duplicate.save(update_fields=fields)

    # 重复账号软删并禁用登录；User 不合并不删除（登录标识隔离，恢复留余地）
    _disable_user_account(duplicate.user)

    _log(
        request, '合并球员', 'player', primary.id, before,
        {'primary_id': primary.id, 'duplicate_id': duplicate.id, 'filled_fields': filled,
         'reason': reason},
    )
    return {
        'primary_id': primary.id,
        'duplicate_id': duplicate.id,
        'merged': True,
        'filled_fields': filled,
    }


# ---------------------------------------------------------------------------
# 阶段三：数据权限 scope（只读 / 写 统一走 apply_scope）
# ---------------------------------------------------------------------------

def apply_scope(qs, user, kind: str = 'player'):
    """按用户 scope 过滤查询集。kind='player' 过滤 AthleteProfile，'coach' 过滤 CoachProfile。

    - admin / superuser → 全量不过滤；
    - province → 省下辖；
    - city → 市下辖；
    - school → 该校下属；
    - 范围管理员没有配置对应 scope → 返回空（qs.none()）。

    球员 scope 同时覆盖直挂 school 与挂 team 两种档案；
    教练档案无 school/team 字段，通过反向 Team.teams 关联。
    """
    if user is None or user.is_superuser or user.role == UserRole.ADMIN:
        return qs

    # 教练：CoachProfile 通过 user.coached_teams 间接关联 Team → School
    if kind == 'coach':
        if user.role == UserRole.PROVINCE and user.scope_province_id:
            return qs.filter(user__coached_teams__school__city__province=user.scope_province).distinct()
        if user.role == UserRole.CITY and user.scope_city_id:
            return qs.filter(user__coached_teams__school__city=user.scope_city).distinct()
        if user.role == UserRole.SCHOOL and user.scope_school_id:
            return qs.filter(user__coached_teams__school=user.scope_school).distinct()
        return qs.none()

    # 球员：同时覆盖直挂 school 与挂 team 两种档案
    if user.role == UserRole.PROVINCE and user.scope_province_id:
        return qs.filter(
            Q(team__school__city__province=user.scope_province)
            | Q(school__city__province=user.scope_province),
        )
    if user.role == UserRole.CITY and user.scope_city_id:
        return qs.filter(
            Q(team__school__city=user.scope_city) | Q(school__city=user.scope_city),
        )
    if user.role == UserRole.SCHOOL and user.scope_school_id:
        return qs.filter(Q(team__school=user.scope_school) | Q(school=user.scope_school))
    return qs.none()


def _ensure_in_scope(user, profile, kind: str = 'player') -> None:
    """写操作前的对象级 scope 校验：不在管辖范围 → 抛 403。"""
    if user is None or user.is_superuser or user.role == UserRole.ADMIN:
        return
    if not apply_scope(profile.__class__.objects.filter(pk=profile.pk), user, kind).exists():
        raise BusinessException(
            ResponseCode.FORBIDDEN,
            f'无权操作该{"球员" if kind == "player" else "教练员"}',
            status_code=403,
        )


def _ensure_school_in_scope(user, school, action: str = 'create') -> None:
    """校验 school 是否在操作者 scope 内。

    - admin / superuser：不校验，允许 school=None（无学校直挂档案）；
    - scope 角色（school / city / province）：school 必填，且必须在 scope 内；
    - scope 角色未配置对应 scope 字段：直接 403；
    - 范围角色 school=None：403（范围管理员不允许建无归属档案或把学校置空）。

    action='create' 时文案为「无权在该学校创建球员」；
    action='transfer' 时文案为「无权将该球员变更到该学校」。
    """
    label = '无权在该学校创建球员' if action == 'create' else '无权将该球员变更到该学校'
    if user is None or user.is_superuser or user.role == UserRole.ADMIN:
        return

    # 范围管理员不允许建无归属档案 / 把学校置空
    if school is None:
        raise BusinessException(
            ResponseCode.FORBIDDEN,
            '范围管理员必须指定学校',
            status_code=403,
        )

    if user.role == UserRole.SCHOOL:
        if not user.scope_school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if school.id != user.scope_school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    # 提前 .select_related('city__province') 以防未预取
    school = School.objects.select_related('city__province').get(pk=school.pk)

    if user.role == UserRole.CITY:
        if not user.scope_city_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if school.city_id != user.scope_city_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    if user.role == UserRole.PROVINCE:
        if not user.scope_province_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if school.city.province_id != user.scope_province_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    # 其他 role（personal / athlete / coach）理论上不会走到这里（被 ADMIN_OR_SCOPED 挡了）
    raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)


def _ensure_team_in_scope(user, team) -> None:
    """校验 team 是否在操作者 scope 内。

    - admin / superuser：不校验，允许 team=None（解绑球队）；
    - scope 角色（school / city / province）：team 必填，且 team.school 必须在 scope 内；
    - scope 角色未配置对应 scope 字段：直接 403；
    - 范围角色 team=None：403（解绑后球员脱离所有人的管辖范围，与置空学校同理）。
    """
    if user is None or user.is_superuser or user.role == UserRole.ADMIN:
        return

    # 范围管理员不允许解绑球队（会导致球员脱离所有人管辖）
    if team is None:
        raise BusinessException(
            ResponseCode.FORBIDDEN,
            '范围管理员必须指定目标球队',
            status_code=403,
        )

    # 提前预取 team.school.city.province
    team = Team.objects.select_related('school__city__province').get(pk=team.pk)
    label = '无权将该球员变更到该球队'

    if user.role == UserRole.SCHOOL:
        if not user.scope_school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if team.school_id != user.scope_school_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    if user.role == UserRole.CITY:
        if not user.scope_city_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if team.school.city_id != user.scope_city_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    if user.role == UserRole.PROVINCE:
        if not user.scope_province_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        if team.school.city.province_id != user.scope_province_id:
            raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)
        return

    raise BusinessException(ResponseCode.FORBIDDEN, label, status_code=403)


# ---------------------------------------------------------------------------
# 阶段三：批量导出（openpyxl + StreamingHttpResponse）
# ---------------------------------------------------------------------------

EXPORT_MAX_ROWS = 5000

PLAYER_EXPORT_FIELDS = (
    'school_name', 'grade', 'name', 'gender', 'position', 'jersey_no',
    'height_cm', 'weight_kg', 'phone_masked', 'id_card_masked',
    'status', 'eligibility_status', 'is_public', 'created_at',
)
COACH_EXPORT_FIELDS = (
    'school_name', 'name', 'phone_masked', 'id_card_masked',
    'qualification', 'status', 'is_public', 'created_at',
)


def _player_export_queryset(request, params):
    qs = (AthleteProfile.objects
          .select_related('team__school', 'school')
          .order_by('created_at'))
    qs = apply_scope(qs, request.user, kind='player')
    if params.get('deleted'):
        qs = qs.filter(deleted_at__isnull=False)
    else:
        qs = qs.filter(deleted_at__isnull=True)
    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    if params.get('id_card'):
        qs = qs.filter(id_card__iexact=params['id_card'])
    if params.get('keyword'):
        kw = params['keyword']
        qs = qs.filter(Q(name__icontains=kw) | Q(id_card__icontains=kw))
    return qs


def _coach_export_queryset(request, params):
    qs = CoachProfile.objects.prefetch_related('user__coached_teams__school').order_by('-created_at')
    qs = apply_scope(qs, request.user, kind='coach')
    if params.get('deleted'):
        qs = qs.filter(deleted_at__isnull=False)
    else:
        qs = qs.filter(deleted_at__isnull=True)
    if params.get('name'):
        qs = qs.filter(name__icontains=params['name'])
    return qs


def _player_export_row(profile):
    team = profile.team if profile.team_id else None
    school = profile.school if profile.school_id else (team.school if team else None)
    return [
        school.name if school else '',
        profile.grade or '',
        profile.name or '',
        profile.gender or '',
        profile.position or '',
        profile.jersey_no if profile.jersey_no is not None else '',
        profile.height_cm if profile.height_cm is not None else '',
        profile.weight_kg if profile.weight_kg is not None else '',
        mask_phone(profile.user.phone or '') if profile.user_id else '',
        mask_id_card(profile.id_card or '') if profile.id_card else '',
        profile.get_status_display() if profile.status else '',
        profile.get_eligibility_status_display() if profile.eligibility_status else '',
        '是' if profile.is_public else '否',
        format_dt(profile.created_at) if profile.created_at else '',
    ]


def _coach_export_row(profile):
    team = profile.user.coached_teams.first() if profile.user_id else None
    school = team.school if team else None
    return [
        school.name if school else '',
        profile.name or '',
        mask_phone(profile.user.phone or '') if profile.user_id else '',
        mask_id_card(profile.id_card or '') if profile.id_card else '',
        profile.qualification_path or '',
        profile.get_status_display() if profile.status else '',
        '是' if profile.is_public else '否',
        format_dt(profile.created_at) if profile.created_at else '',
    ]


def _write_xlsx(headers, rows_iterable, title: str) -> io.BytesIO:
    from openpyxl import Workbook  # 延迟导入，降低冷启动开销

    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(list(headers))
    for row in rows_iterable:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def export_players(request, params: dict) -> StreamingHttpResponse:
    qs = _player_export_queryset(request, params)
    total = qs.count()
    if total > EXPORT_MAX_ROWS:
        raise BusinessException(
            ResponseCode.BUSINESS_ERROR, f'导出超过{EXPORT_MAX_ROWS}行，请缩小筛选范围',
        )

    buffer = _write_xlsx(
        PLAYER_EXPORT_FIELDS, (_player_export_row(p) for p in qs.iterator(chunk_size=2000)),
        '球员名单',
    )
    filename = f'players_{date.today().strftime("%Y%m%d")}.xlsx'
    # RFC 5987 兼容 UTF-8 文件名
    disposition = (
        f"attachment; filename=\"players.xlsx\"; "
        f"filename*=UTF-8''{filename}"
    )
    resp = StreamingHttpResponse(
        buffer,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': disposition},
    )
    _log(request, '导出球员', 'player', 0, {'filename': filename}, {'count': total})
    return resp


def export_coaches(request, params: dict) -> StreamingHttpResponse:
    qs = _coach_export_queryset(request, params)
    total = qs.count()
    if total > EXPORT_MAX_ROWS:
        raise BusinessException(
            ResponseCode.BUSINESS_ERROR, f'导出超过{EXPORT_MAX_ROWS}行，请缩小筛选范围',
        )

    buffer = _write_xlsx(
        COACH_EXPORT_FIELDS, (_coach_export_row(c) for c in qs.iterator(chunk_size=2000)),
        '教练员名单',
    )
    filename = f'coaches_{date.today().strftime("%Y%m%d")}.xlsx'
    disposition = (
        f"attachment; filename=\"coaches.xlsx\"; "
        f"filename*=UTF-8''{filename}"
    )
    resp = StreamingHttpResponse(
        buffer,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': disposition},
    )
    _log(request, '导出教练员', 'coach', 0, {'filename': filename}, {'count': total})
    return resp


# ---------------------------------------------------------------------------
# 阶段三：批量导入（openpyxl 读 xlsx）
# ---------------------------------------------------------------------------

IMPORT_MAX_ROWS = 500
IMPORT_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
IMPORT_FAIL_ROLLBACK_RATIO = 0.8

IMPORT_HEADERS = [
    '姓名', '手机号', '身份证号', '学校名称', '年级',
    '性别', '位置', '球衣号', '身高cm', '体重kg',
]


def import_template(request) -> StreamingHttpResponse:
    """返回带表头 + 示例 + 必填说明的导入模板。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = '球员导入模板'
    ws.append(IMPORT_HEADERS)
    ws.append([
        '张三', '13800000001', '110101199003077758', '测试学校', '初二',
        'male', '前锋', 10, 170, 60,
    ])
    ws.append([])
    ws.append(['说明：前四列为必填；性别填 male/female；身高体重单位 cm/kg；身份证需校验位正确'])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f'players_import_template_{date.today().strftime("%Y%m%d")}.xlsx'
    disposition = (
        f"attachment; filename=\"players_import_template.xlsx\"; "
        f"filename*=UTF-8''{filename}"
    )
    return StreamingHttpResponse(
        buffer,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': disposition},
    )


def import_players(request, file) -> dict:
    """导入球员 xlsx。逐行独立事务 + 单文件失败率>80% 整体回滚。"""
    if file is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, '未上传文件')
    if file.size and file.size > IMPORT_MAX_FILE_BYTES:
        raise BusinessException(
            ResponseCode.PARAM_INVALID, f'文件超过{IMPORT_MAX_FILE_BYTES // 1024 // 1024}MB',
        )

    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(file.read()), read_only=True, data_only=True)
    except Exception as exc:
        raise BusinessException(ResponseCode.PARAM_INVALID, f'文件格式不合法: {exc}')

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) <= 1:
        raise BusinessException(ResponseCode.PARAM_INVALID, '文件为空或只有表头')
    body_rows = rows[1:]
    if len(body_rows) > IMPORT_MAX_ROWS:
        raise BusinessException(
            ResponseCode.PARAM_INVALID, f'单次最多导入{IMPORT_MAX_ROWS}行',
        )

    total = len(body_rows)
    success_ids = []
    failed = []
    now = timezone.now()

    for idx, row in enumerate(body_rows, start=2):  # Excel 行号从 1 起，跳过表头
        try:
            data = _parse_import_row(row)
            with transaction.atomic():
                # 用已有的 create_player 逻辑（但用 import_scope 里创建，
                # 这里直接 inline 复用校验 + 新建）
                _ensure_phone_available(data['phone'])
                _ensure_id_card_available(data['id_card'], AthleteProfile, None)
                school = _get_school_by_name(data['school_name'])
                user = User.objects.create_user(
                    username=data['phone'], phone=data['phone'],
                    role=UserRole.ATHLETE, name=data['name'],
                    password=settings.DEFAULT_RESET_PASSWORD,
                )
                profile = AthleteProfile.objects.create(
                    user=user, name=data['name'], id_card=data['id_card'],
                    school=school, gender=data.get('gender') or None,
                    grade=data.get('grade') or None,
                    position=data.get('position') or '',
                    jersey_no=data.get('jersey_no'),
                    height_cm=data.get('height_cm'),
                    weight_kg=data.get('weight_kg'),
                    status=ProfileStatus.PENDING,
                )
            success_ids.append(profile.id)
        except BusinessException as exc:
            failed.append({'row': idx, 'name': (row[0] or '')[:50], 'errors': [str(exc.msg)]})
        except Exception as exc:  # pragma: no cover - 兜底
            failed.append({'row': idx, 'name': (row[0] or '')[:50], 'errors': [f'{exc}']})

    if total >= 3 and len(failed) / total > IMPORT_FAIL_ROLLBACK_RATIO:
        # 整体回滚成功行，防误传（至少 3 行才判"整体有误"，避免单行文件全失败被过度回滚）
        for pid in success_ids:
            try:
                profile = AthleteProfile.objects.get(pk=pid)
                user = profile.user
                user.delete()
                profile.delete()
            except AthleteProfile.DoesNotExist:
                pass
        _log(request, '批量导入球员', 'player', 0,
             {'file': getattr(file, 'name', '')},
             {'total': total, 'success': 0, 'failed': len(failed), 'rolled_back': True})
        raise BusinessException(
            ResponseCode.BUSINESS_ERROR,
            f'失败行超过 80%，整体回滚，请检查文件内容后重试',
        )

    _log(request, '批量导入球员', 'player', 0,
         {'file': getattr(file, 'name', '')},
         {'total': total, 'success': len(success_ids), 'failed': len(failed)})
    return {'total': total, 'success': len(success_ids), 'failed': failed}


def _parse_import_row(row) -> dict:
    from .serializers import validate_id_card  # 局部引入避免循环

    name = str(row[0] or '').strip()
    phone = str(row[1] or '').strip()
    id_card_raw = str(row[2] or '').strip()
    school_name = str(row[3] or '').strip()
    grade = str(row[4] or '').strip() if len(row) > 4 else ''
    gender = str(row[5] or '').strip() if len(row) > 5 else ''
    position = str(row[6] or '').strip() if len(row) > 6 else ''
    jersey_no = row[7] if len(row) > 7 else None
    height_cm = row[8] if len(row) > 8 else None
    weight_kg = row[9] if len(row) > 9 else None

    if not name:
        raise BusinessException(ResponseCode.PARAM_INVALID, '姓名不能为空')
    if not phone:
        raise BusinessException(ResponseCode.PARAM_INVALID, '手机号不能为空')
    if not id_card_raw:
        raise BusinessException(ResponseCode.PARAM_INVALID, '身份证号不能为空')
    if not school_name:
        raise BusinessException(ResponseCode.PARAM_INVALID, '学校名称不能为空')

    import re
    if not re.match(r'^1[3-9]\d{9}$', phone):
        raise BusinessException(ResponseCode.PARAM_INVALID, '手机号格式不正确')

    id_card = validate_id_card(id_card_raw)

    if gender and gender not in ('male', 'female'):
        raise BusinessException(ResponseCode.PARAM_INVALID, '性别仅支持 male / female')

    def _int(value, field_name):
        if value in (None, ''):
            return None
        try:
            iv = int(float(value))
        except (ValueError, TypeError):
            raise BusinessException(ResponseCode.PARAM_INVALID, f'{field_name} 必须为整数')
        if iv < 0:
            raise BusinessException(ResponseCode.PARAM_INVALID, f'{field_name} 不能为负')
        return iv

    return {
        'name': name, 'phone': phone, 'id_card': id_card, 'school_name': school_name,
        'grade': grade or None,
        'gender': gender or None,
        'position': position or None,
        'jersey_no': _int(jersey_no, '球衣号'),
        'height_cm': _int(height_cm, '身高'),
        'weight_kg': _int(weight_kg, '体重'),
    }


def _get_school_by_name(name: str) -> School:
    school = School.objects.filter(name=name).first()
    if school is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, f'学校不存在: {name}')
    return school


# ---------------------------------------------------------------------------
# 阶段三：批量上传头像（多文件 multipart）
# ---------------------------------------------------------------------------

AVATAR_MAX_FILES = 50
AVATAR_MAX_FILE_BYTES = 2 * 1024 * 1024
AVATAR_ALLOWED_MIME = {'image/jpeg', 'image/png'}
AVATAR_EXT_MAP = {'image/jpeg': '.jpg', 'image/png': '.png'}


def _detect_image_mime(file) -> str | None:
    """按文件头嗅探 MIME（不信任扩展名）。"""
    header = file.read(12)
    file.seek(0)
    if header[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if header[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    return None


def upload_avatars(request, files) -> dict:
    if not files:
        raise BusinessException(ResponseCode.PARAM_INVALID, '未上传任何文件')
    if len(files) > AVATAR_MAX_FILES:
        raise BusinessException(
            ResponseCode.PARAM_INVALID, f'单次最多上传{AVATAR_MAX_FILES}个文件',
        )

    total = len(files)
    success = 0
    skipped = []

    # 确保头像目录存在
    avatar_dir = os.path.join(settings.MEDIA_ROOT, 'avatars')
    os.makedirs(avatar_dir, exist_ok=True)

    for f in files:
        filename = f.name or ''
        # 约定：{player_id}.jpg/png
        stem, dot, _ext = filename.rpartition('.')
        if not dot or not stem.isdigit():
            skipped.append({'file': filename, 'reason': '文件名格式应为 {player_id}.jpg/png'})
            continue
        player_id = int(stem)

        mime = _detect_image_mime(f)
        if mime not in AVATAR_ALLOWED_MIME:
            skipped.append({'file': filename, 'reason': '仅支持 jpg / png（按文件内容嗅探）'})
            continue

        if f.size and f.size > AVATAR_MAX_FILE_BYTES:
            skipped.append({'file': filename, 'reason': f'单个文件超过{AVATAR_MAX_FILE_BYTES // 1024 // 1024}MB'})
            continue

        try:
            profile = AthleteProfile.objects.get(pk=player_id, deleted_at__isnull=True)
        except AthleteProfile.DoesNotExist:
            skipped.append({'file': filename, 'reason': f'球员 {player_id} 不存在或已删除'})
            continue

        # 权限校验：文件对应的球员必须在当前用户 scope 内
        if request.user.role not in ('admin',) and not request.user.is_superuser:
            if not apply_scope(
                AthleteProfile.objects.filter(pk=player_id), request.user, kind='player',
            ).exists():
                skipped.append({'file': filename, 'reason': '无权操作该球员'})
                continue

        ext = AVATAR_EXT_MAP[mime]
        target_name = f'{player_id}{ext}'
        target_path = os.path.join(avatar_dir, target_name)
        # 覆盖旧文件
        with open(target_path, 'wb+') as dest:
            for chunk in f.chunks():
                dest.write(chunk)

        rel_path = f'avatars/{target_name}'
        profile.avatar_path = rel_path
        profile.updated_at = timezone.now()
        profile.save(update_fields=['avatar_path', 'updated_at'])
        success += 1

    _log(request, '批量上传头像', 'player', 0,
         {'files': [f.name for f in files]},
         {'total': total, 'success': success, 'skipped': skipped})
    return {'total': total, 'success': success, 'skipped': skipped}
