"""eventops 业务服务。

- 事件快照 / 版本创建
- 操作日志
- 比分 vs 事件进球一致性校验
- 赛事归档 / 取消归档（强校验）
- 文件落盘（PRIVATE_STORAGE_ROOT）
"""
import hashlib
import os
import uuid
from datetime import timezone as _tz

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AthleteProfile
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.live.models import Match, MatchStatus
from apps.org.models import Team

from .models import (
    EventRevision,
    MatchDocument,
    MatchDocumentVersion,
    MatchEvent,
    MatchOfficial,
    MatchRecord,
    OperationLog,
    Referee,
    EventType,
    EventPeriod,
)


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------

GOAL_EVENT_TYPES = (EventType.GOAL, EventType.PENALTY_GOAL)
NON_SHOOTOUT_PERIODS = (
    EventPeriod.FIRST, EventPeriod.SECOND, EventPeriod.EXTRA_1, EventPeriod.EXTRA_2,
)


def get_trace_id(request) -> str:
    return getattr(request, 'trace_id', '') or ''


def get_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def log_operation(user, action, resource_type, resource_id,
                  before_data=None, after_data=None, ip='', trace_id=''):
    user_id = getattr(user, 'id', None)
    user_role = getattr(user, 'role', '') or ''
    user_name = getattr(user, 'name', '') or getattr(user, 'username', '') or ''
    OperationLog.objects.create(
        action=action,
        resource_type=resource_type,
        resource_id=int(resource_id) if resource_id is not None else 0,
        user_id=user_id,
        user_role=user_role,
        user_name=user_name,
        before_data=before_data,
        after_data=after_data,
        ip=ip or '',
        trace_id=trace_id or '',
    )


# ---------------------------------------------------------------------------
# 事件快照 / 版本
# ---------------------------------------------------------------------------

def snapshot_event(event: MatchEvent) -> dict:
    return {
        'match_id': event.match_id,
        'event_type': event.event_type,
        'period': event.period,
        'minute': event.minute,
        'stoppage_minute': event.stoppage_minute,
        'team_id': event.team_id,
        'athlete_id': event.athlete_id,
        'related_athlete_id': event.related_athlete_id,
        'description': event.description,
        'version': event.version,
        'is_deleted': event.is_deleted,
        'deleted_at': event.deleted_at.isoformat() if event.deleted_at else None,
        'delete_reason': event.delete_reason,
    }


def create_event_revision(event: MatchEvent, change_type: str,
                          reason: str = '', changed_by=None, trace_id: str = ''):
    # EventRevision 的 version_no 独立自增，避免 finalize 等全局事件与
    # event.version 重复（finalize 不修改 event.version 但仍需留痕）
    next_no = EventRevision.objects.filter(event=event).count() + 1
    EventRevision.objects.create(
        event=event,
        version_no=next_no,
        change_type=change_type,
        snapshot=snapshot_event(event),
        change_reason=reason or '',
        changed_by=getattr(changed_by, 'id', changed_by) if changed_by else None,
        trace_id=trace_id or '',
    )


# ---------------------------------------------------------------------------
# 比分 vs 事件进球一致性
# ---------------------------------------------------------------------------

def check_goal_vs_score(match: Match, home_score=None, away_score=None) -> dict:
    """计算事件进球与赛果比分的差异。

    参数 ``home_score`` / ``away_score`` 可显式传入（发布前校验尚未落库的比分）；
    不传则直接用 ``match.home_score`` / ``match.away_score``。

    口径：
    - 计入：GOAL、PENALTY_GOAL
    - 乌龙球：计入对方球队
    - 点球大战（period=shootout）不计入常规比分比对
    """
    if home_score is None or away_score is None:
        home_score = match.home_score
        away_score = match.away_score

    events = MatchEvent.objects.filter(
        match=match, is_deleted=False, event_type__in=GOAL_EVENT_TYPES,
    ).exclude(period=EventPeriod.SHOOTOUT)

    home_team_id = match.home_team_id
    away_team_id = match.away_team_id
    home_goals = 0
    away_goals = 0
    details = {'goal': {'home': 0, 'away': 0}, 'penalty_goal': {'home': 0, 'away': 0}}
    for e in events:
        tid = e.team_id
        side = 'home' if tid == home_team_id else ('away' if tid == away_team_id else None)
        if side is None:
            continue
        if e.event_type == EventType.GOAL:
            details['goal'][side] += 1
        elif e.event_type == EventType.PENALTY_GOAL:
            details['penalty_goal'][side] += 1
        if side == 'home':
            home_goals += 1
        else:
            away_goals += 1

    diff_home = home_score - home_goals
    diff_away = away_score - away_goals
    return {
        'result': {'home_score': int(home_score), 'away_score': int(away_score)},
        'events': {
            'home_goals': home_goals, 'away_goals': away_goals, 'details': details,
        },
        'consistent': diff_home == 0 and diff_away == 0,
        'diff': {'home': diff_home, 'away': diff_away},
    }


# ---------------------------------------------------------------------------
# 赛事归档 / 取消归档
# ---------------------------------------------------------------------------

class FinalizeError(BusinessException):
    pass


def finalize_match(match: Match, user, note: str = '') -> MatchRecord:
    """归档赛事数据（幂等）。

    强校验：
    1. 比赛状态必须是 finished
    2. 已指定主裁判
    3. 事件进球与比分一致
    """
    if match.status != MatchStatus.FINISHED:
        raise FinalizeError(ResponseCode.MATCH_FINISHED_LOCKED, '比赛尚未结束，禁止归档')

    if not MatchOfficial.objects.filter(match=match, role='chief').exists():
        raise FinalizeError(ResponseCode.BUSINESS_ERROR, '未指定主裁判，禁止归档')

    check = check_goal_vs_score(match)
    if not check['consistent']:
        raise FinalizeError(
            ResponseCode.EVENT_MATCH_CONSISTENCY,
            f'事件进球({check["events"]["home_goals"]}:{check["events"]["away_goals"]}) '
            f'与赛果比分({check["result"]["home_score"]}:{check["result"]["away_score"]})不一致，禁止归档',
        )

    now = timezone.now()
    with transaction.atomic():
        record, _ = MatchRecord.objects.get_or_create(match=match)
        if record.events_locked:
            return record  # 幂等
        record.events_locked = True
        record.locked_at = now
        record.locked_by = getattr(user, 'id', user) if user else None
        record.lock_note = (note or '').strip() or '赛事归档'
        record.unlocked_at = None
        record.unlocked_by = None
        record.unlock_reason = ''
        record.save()
    return record


def unfinalize_match(match: Match, user, reason: str = '') -> MatchRecord:
    """取消归档（幂等）。"""
    record, _ = MatchRecord.objects.get_or_create(match=match)
    if not record.events_locked:
        return record
    record.events_locked = False
    record.unlocked_at = timezone.now()
    record.unlocked_by = getattr(user, 'id', user) if user else None
    record.unlock_reason = (reason or '').strip() or '取消归档'
    record.locked_at = None
    record.locked_by = None
    record.lock_note = record.lock_note or ''
    record.save()
    return record


def is_match_locked(match: Match) -> bool:
    return MatchRecord.objects.filter(match=match, events_locked=True).exists()


# ---------------------------------------------------------------------------
# 文件落盘（私有存储）
# ---------------------------------------------------------------------------

def save_uploaded_file(doc: MatchDocument, version_no: int, file_obj) -> tuple:
    """把已校验过的文件写入 PRIVATE_STORAGE_ROOT 并返回 (relative_path, sha256, size, ext, mime)."""
    root = getattr(settings, 'PRIVATE_STORAGE_ROOT', None) or settings.MEDIA_ROOT
    root = os.path.abspath(root)
    ext = os.path.splitext(file_obj.name or '')[1].lstrip('.').lower() or 'bin'
    uuid_str = uuid.uuid4().hex
    rel_path = f'event_docs/{doc.match_id}/{doc.doc_type}/{doc.id}/v{version_no}_{uuid_str}.{ext}'
    full_path = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # 读全量写入 + 算 SHA-256
    file_obj.seek(0)
    content = file_obj.read()
    with open(full_path, 'wb') as f:
        f.write(content)
    sha256 = hashlib.sha256(content).hexdigest()

    return rel_path, sha256, len(content), ext, (file_obj.content_type or 'application/octet-stream')
