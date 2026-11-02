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
from .models import AdminOperationLog, CoachObjection

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

def _get_player(pk: int, for_update: bool = False, with_deleted: bool = False) -> AthleteProfile:
    qs = AthleteProfile.objects.select_related('user', 'team__school', 'school')
    if for_update:
        # 公示类操作的「查 → 判断 → 改」需要行锁，避免并发重复公示
        qs = qs.select_for_update()
    try:
        if with_deleted:
            return qs.get(pk=pk)
        return qs.get(pk=pk, deleted_at__isnull=True)
    except AthleteProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '球员不存在', status_code=404)


def _player_list_item(profile: AthleteProfile) -> dict:
    team = profile.team if profile.team_id else None
    school = _school_of(profile)
    return {
        'id': profile.id,
        'name': profile.name,
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
    data.update({
        'id_card': mask_id_card(profile.id_card),
        'phone': mask_phone(profile.user.phone or '') if profile.user_id else '',
        'enrollment_cert': profile.enrollment_cert_path,
        'reject_reason': profile.reject_reason,
        'updated_at': format_dt(profile.updated_at),
    })
    return data


def list_players(request, params: dict) -> dict:
    qs = (AthleteProfile.objects
          .select_related('team__school', 'school')
          .order_by('-created_at'))
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


def get_player(pk: int) -> dict:
    return _player_detail(_get_player(pk))


@transaction.atomic
def create_player(request, data: dict) -> dict:
    phone = data['phone']
    id_card = data['id_card']
    _ensure_phone_available(phone)
    _ensure_id_card_available(id_card, AthleteProfile, None)

    school = _get_school(data['school_id']) if data.get('school_id') else None
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
    player = _get_player(pk)
    before = _player_detail(player)

    changed = []
    field_map = {
        'name': 'name',
        'jersey_no': 'jersey_no',
        'position': 'position',
        'avatar': 'avatar_path',
        'id_card': 'id_card',
        'grade': 'grade',
        'height_cm': 'height_cm',
        'weight_kg': 'weight_kg',
    }
    if data.get('id_card'):
        _ensure_id_card_available(data['id_card'], AthleteProfile, player.pk)
    if 'school_id' in data:
        school_id = data['school_id']
        player.school = _get_school(school_id) if school_id else None
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
    player = _get_player(pk)
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
    player = _get_player(pk, for_update=True)
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
    player = _get_player(pk, for_update=True)
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
    player = _get_player(pk, for_update=True)
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
    player = _get_player(pk)
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
    player = _get_player(pk, with_deleted=True)
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
    player = _get_player(pk)
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

def _get_coach(pk: int, for_update: bool = False, with_deleted: bool = False) -> CoachProfile:
    qs = CoachProfile.objects.select_related('user')
    if for_update:
        # 公示类操作的「查 → 判断 → 改」需要行锁，避免并发重复公示
        qs = qs.select_for_update()
    try:
        if with_deleted:
            return qs.get(pk=pk)
        return qs.get(pk=pk, deleted_at__isnull=True)
    except CoachProfile.DoesNotExist:
        raise BusinessException(ResponseCode.NOT_FOUND, '教练员不存在', status_code=404)


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
    qs = CoachProfile.objects.order_by('-created_at')
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
    coach = _get_coach(pk, for_update=True)
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
    coach = _get_coach(pk, for_update=True)
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
    coach = _get_coach(pk, for_update=True)
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
    coach = _get_coach(pk)
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
    coach = _get_coach(pk, with_deleted=True)
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
    coach = _get_coach(pk)
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
    coach = _get_coach(coach_id)
    obj = CoachObjection.objects.create(coach=coach, **data)
    _log(request, '代录公示异议', 'coach', coach.id, None, _objection_item(obj))
    return _objection_item(obj)


def list_objections(request, coach_id: int, params: dict) -> dict:
    coach = _get_coach(coach_id)
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
