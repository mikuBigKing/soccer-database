"""赛事报告服务：数据组装、指纹、生成状态机。"""
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.common.formatting import format_dt
from apps.live.models import EventType, Match, MatchStatus

from . import llm, prompts
from .models import MatchReport, ReportStatus

SIDE_LABELS = {'home': '主队', 'away': '客队'}


def get_match(match_id: int) -> Match:
    match = Match.objects.filter(pk=match_id).first()
    if match is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '比赛不存在', status_code=404)
    return match


def _side_counts(events: list, event_type: str) -> dict:
    counts = {'home': 0, 'away': 0}
    for event in events:
        if event.type == event_type and event.team_side in counts:
            counts[event.team_side] += 1
    return counts


def _goal_buckets(events: list) -> dict:
    buckets = {label: 0 for label, _, _ in prompts.BUCKET_LABELS}
    for event in events:
        if event.type != EventType.GOAL:
            continue
        for label, low, high in prompts.BUCKET_LABELS:
            if low <= event.minute <= high:
                buckets[label] += 1
                break
    return buckets


def _score_changes(events: list) -> tuple[list, str]:
    """按进球顺序重算比分变化，同时返回先进球方。"""
    home = away = 0
    changes = []
    first_scorer = None
    for event in events:
        if event.type != EventType.GOAL or event.team_side not in SIDE_LABELS:
            continue
        if event.team_side == 'home':
            home += 1
        else:
            away += 1
        if first_scorer is None:
            first_scorer = event.team_side
        changes.append({'minute': event.minute, 'home': home, 'away': away})
    return changes, SIDE_LABELS.get(first_scorer)


def _referees(match: Match) -> dict:
    grouped = {'chief': [], 'assistant': [], 'fourth': []}
    for referee in match.referees.all():
        grouped.setdefault(referee.role, []).append(referee.name)
    return grouped


def build_payload(match: Match) -> dict:
    """把比赛数据整理成 prompt 输入，派生统计在服务层先算好。"""
    events = list(match.events.all())
    derived = {
        'goals': _side_counts(events, EventType.GOAL),
        'yellow': _side_counts(events, EventType.YELLOW),
        'red': _side_counts(events, EventType.RED),
        'substitution': _side_counts(events, EventType.SUBSTITUTION),
        'goal_buckets': _goal_buckets(events),
        'has_red': any(e.type == EventType.RED for e in events),
    }
    changes, first_scorer_label = _score_changes(events)
    derived['score_changes'] = changes
    derived['first_scorer_label'] = first_scorer_label
    derived['data_quality'] = {
        'events_total': len(events),
        'has_lineup': bool(match.lineup_home or match.lineup_away),
        'has_home_events': any(e.team_side == 'home' for e in events),
        'has_away_events': any(e.team_side == 'away' for e in events),
        'text_only': bool(events) and all(e.type == EventType.TEXT for e in events),
    }

    return {
        'match': {
            'group_type': match.group_type,
            'group_type_label': match.get_group_type_display(),
            'home_team': match.home_team_name,
            'away_team': match.away_team_name,
            'home_score': match.home_score,
            'away_score': match.away_score,
            'match_time': format_dt(match.match_time),
            'venue': match.venue,
        },
        'referees': _referees(match),
        'events': [
            {
                'minute': e.minute,
                'type': e.type,
                'type_label': e.get_type_display(),
                'side': e.team_side,
                'team': match.home_team_name if e.team_side == 'home'
                else (match.away_team_name if e.team_side == 'away' else None),
                'player_no': e.player_no,
                'player_name': e.player_name,
                'related': e.related_player_name,
                'content': e.content,
            }
            for e in events
        ],
        'lineups': {
            'home': match.lineup_home or [],
            'away': match.lineup_away or [],
        },
        'derived': derived,
    }


def fingerprint_of(match: Match, payload: dict) -> str:
    """事件或阵容变化才需要重新生成，据此计算输入指纹。"""
    raw = json.dumps(
        {
            'match': match.pk,
            'score': [payload['match']['home_score'], payload['match']['away_score']],
            'events': [
                [e['minute'], e['type'], e['side'], e['player_name'], e['content']]
                for e in payload['events']
            ],
            'lineups': payload['lineups'],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _mock_for(payload: dict) -> dict:
    """mock 模式下按真实数据拼一份可读报告，便于前端联调。"""
    m = payload['match']
    derived = payload['derived']
    goals = [e for e in payload['events'] if e['type'] == EventType.GOAL]
    home, away = m['home_team'], m['away_team']

    key_moments = [
        {
            'minute': e['minute'],
            'type': 'goal',
            'description': f"{e['team']} {e['player_no'] or ''}号 {e['player_name'] or ''} 完成破门".strip(),
            'impact': '改写比分',
        }
        for e in goals[:5]
    ]
    narrative = (
        f"{m['group_type_label']}，{home}与{away}在{m['venue'] or '主场'}展开较量。"
        f"全场比分 {m['home_score']} - {m['away_score']}，"
        f"主队进球 {derived['goals']['home']} 次、客队 {derived['goals']['away']} 次，"
        f"黄牌 {derived['yellow']['home'] + derived['yellow']['away']} 张"
        f"{'，并出现红牌' if derived['has_red'] else ''}。"
        f"{'先进球的一方是' + derived['first_scorer_label'] + '，' if derived['first_scorer_label'] else ''}"
        "比赛节奏随比分变化逐步加快。"
    )
    chapters = [
        {'title': '上半场：试探与首开纪录', 'content': f'双方在上半场以稳守为主，{home}率先创造机会。',
         'minute_range': '1-45', 'emotion': '紧张'},
        {'title': '下半场：体能与调整', 'content': '易边后双方均做出人员调整，比赛对抗强度上升。',
         'minute_range': '46-90', 'emotion': '胶着'},
    ]
    notes = ['当前为 mock 模式生成，文本用于联调，不代表真实比赛']

    return {
        'narrative': narrative,
        'chapters': chapters,
        'key_moments': key_moments,
        'highlights': [f"全场进球 {derived['goals']['home'] + derived['goals']['away']} 个"],
        'data_notes': notes,
        'confidence': 80,
    }


def generate_report(match_id: int, user_id: int = None, force: bool = False) -> MatchReport:
    if not settings.REPORT_ENABLED:
        raise BusinessException(ResponseCode.AI_SERVICE_FAILED, '报告生成功能已关闭')

    match = get_match(match_id)
    if match.status != MatchStatus.FINISHED:
        raise BusinessException(ResponseCode.MATCH_FINISHED_LOCKED, '比赛结束后才可生成报告')

    payload = build_payload(match)
    if not payload['events']:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '本场无可分析的事件，暂不可生成报告')

    fingerprint = fingerprint_of(match, payload)
    existing = MatchReport.objects.filter(match=match).first()
    if (
        not force
        and existing
        and existing.status == ReportStatus.SUCCEEDED
        and existing.fingerprint == fingerprint
        and existing.has_content
    ):
        return existing

    with transaction.atomic():
        report, _ = MatchReport.objects.update_or_create(
            match=match,
            defaults={
                'status': ReportStatus.GENERATING,
                'fingerprint': fingerprint,
                'prompt_version': prompts.PROMPT_VERSION,
                'error_msg': None,
                'generated_by': user_id,
            },
        )

    try:
        result, usage = llm.chat_json(
            prompts.SYSTEM, prompts.build_user(payload), mock=_mock_for(payload)
        )
    except BusinessException as exc:
        with transaction.atomic():
            report.status = ReportStatus.FAILED
            report.error_msg = str(exc.msg)[:500]
            report.save(update_fields=['status', 'error_msg', 'updated_at'])
        raise

    with transaction.atomic():
        report.status = ReportStatus.SUCCEEDED
        report.narrative = result['narrative']
        report.chapters = result['chapters']
        report.key_moments = result['key_moments']
        report.highlights = result['highlights']
        report.data_notes = result['data_notes']
        report.confidence = result['confidence']
        report.model_name = usage['model']
        report.input_tokens = usage['input_tokens']
        report.output_tokens = usage['output_tokens']
        report.duration_ms = usage['duration_ms']
        report.error_msg = None
        report.generated_at = timezone.now()
        report.save()
    return report
