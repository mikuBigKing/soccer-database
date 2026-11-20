"""榜单聚合：选手进球榜 / 分组进球统计 / 球队进球榜。

口径：只统计已结束且未删除的比赛；进球只认 `type=goal` 的事件；
选手榜优先按 athlete 关联聚合，无关联时按 player_name 兜底。
结果进 locmem 缓存 60s，比赛结束时通过 invalidate() 立即失效。
"""
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from apps.accounts.models import AthleteProfile
from apps.org.models import GroupType

from .models import EventType, Match, MatchEvent, MatchStatus

CACHE_VERSION_KEY = 'live:leaderboard:version'
CACHE_KEY = 'live:leaderboard:%s:%s:%s'
CACHE_TTL = 60

GOAL_BUCKETS = (
    ('0-15', 0, 15),
    ('16-30', 16, 30),
    ('31-45', 31, 45),
    ('46-60', 46, 60),
    ('61-75', 61, 75),
    ('76-90', 76, 90),
)


def _cache_version() -> int:
    return int(cache.get(CACHE_VERSION_KEY) or 0)


def invalidate() -> None:
    """比赛结束/比分修正后调用，让榜单缓存整体失效。"""
    cache.set(CACHE_VERSION_KEY, _cache_version() + 1, None)


def _group_type_label(value: str) -> str:
    return dict(GroupType.choices).get(value, value)


def _finished_matches(group_type: str = None):
    queryset = Match.objects.filter(status=MatchStatus.FINISHED)
    if group_type:
        queryset = queryset.filter(group_type=group_type)
    return queryset


def _bucket(minute: int) -> str:
    for label, low, high in GOAL_BUCKETS:
        if low <= minute <= high:
            return label
    return GOAL_BUCKETS[-1][0]


def top_scorers(group_type: str = None, limit: int = 10) -> list:
    """选手进球榜：athlete 关联优先，player_name 兜底。"""
    events = MatchEvent.objects.filter(match__in=_finished_matches(group_type), type=EventType.GOAL)

    rows = []
    athlete_ids = list(events.filter(athlete__isnull=False).values_list('athlete_id', flat=True).distinct())
    if athlete_ids:
        profiles = {
            profile.id: profile
            for profile in AthleteProfile.objects.filter(id__in=athlete_ids).select_related('team')
        }
        aggregated = (
            events.filter(athlete__isnull=False)
            .values('athlete_id')
            .annotate(goals=Count('id'))
            .order_by('-goals')
        )
        for item in aggregated:
            profile = profiles.get(item['athlete_id'])
            if profile is None:
                continue
            rows.append({
                'athlete_id': profile.id,
                'name': profile.name,
                'jersey_no': profile.jersey_no,
                'team_id': profile.team_id,
                'team_name': profile.team.name if profile.team else None,
                'group_type': profile.team.group_type if profile.team else None,
                'goals': item['goals'],
                'has_athlete': True,
            })

    fallback = (
        events.filter(athlete__isnull=True, player_name__isnull=False)
        .exclude(player_name='')
        .values(
            'player_name', 'team_side',
            'match__home_team_id', 'match__home_team_name',
            'match__away_team_id', 'match__away_team_name',
            'match__group_type',
        )
        .annotate(goals=Count('id'))
        .order_by('-goals')
    )
    for item in fallback:
        is_home = item['team_side'] == 'home'
        rows.append({
            'athlete_id': None,
            'name': item['player_name'],
            'jersey_no': None,
            'team_id': item['match__home_team_id'] if is_home else item['match__away_team_id'],
            'team_name': item['match__home_team_name'] if is_home else item['match__away_team_name'],
            'group_type': item['match__group_type'],
            'goals': item['goals'],
            'has_athlete': False,
        })

    rows.sort(key=lambda row: (-row['goals'], row['name']))
    rows = rows[:limit]
    for index, row in enumerate(rows, start=1):
        row['rank'] = index
    return rows


def group_goals(group_type: str = None) -> list:
    """分组进球统计：场次 / 总进球 / 场均 / 进球时段分布。"""
    values = list(GroupType.values) if not group_type else [group_type]
    rows = []
    for value in values:
        matches = _finished_matches(value)
        match_count = matches.count()
        goals = MatchEvent.objects.filter(match__in=matches, type=EventType.GOAL).count()
        buckets = {label: 0 for label, _, _ in GOAL_BUCKETS}
        minutes = MatchEvent.objects.filter(
            match__in=matches, type=EventType.GOAL
        ).values_list('minute', flat=True)
        for minute in minutes:
            buckets[_bucket(minute)] += 1
        rows.append({
            'group_type': value,
            'label': _group_type_label(value),
            'matches': match_count,
            'goals': goals,
            'avg_goals': round(goals / match_count, 2) if match_count else 0,
            'goal_buckets': buckets,
        })
    return rows


def team_goals(group_type: str = None) -> list:
    """球队进球榜：按比赛比分聚合进球/失球（只算双方都关联了球队的比赛）。"""
    matches = _finished_matches(group_type).filter(home_team__isnull=False, away_team__isnull=False)
    rows = {}
    for match in matches.values(
        'home_team_id', 'home_team__name', 'home_team__group_type',
        'away_team_id', 'away_team__name', 'away_team__group_type',
        'home_score', 'away_score',
    ):
        home = rows.setdefault(match['home_team_id'], {
            'team_id': match['home_team_id'],
            'team_name': match['home_team__name'],
            'group_type': match['home_team__group_type'],
            'matches': 0, 'goals': 0, 'goals_against': 0,
        })
        away = rows.setdefault(match['away_team_id'], {
            'team_id': match['away_team_id'],
            'team_name': match['away_team__name'],
            'group_type': match['away_team__group_type'],
            'matches': 0, 'goals': 0, 'goals_against': 0,
        })
        home['matches'] += 1
        home['goals'] += match['home_score']
        home['goals_against'] += match['away_score']
        away['matches'] += 1
        away['goals'] += match['away_score']
        away['goals_against'] += match['home_score']
    return sorted(rows.values(), key=lambda row: (-row['goals'], row['goals_against'], row['team_id']))


def payload(group_type: str = None, limit: int = 10) -> dict:
    """榜单汇总，带 60s 缓存。"""
    key = CACHE_KEY % (_cache_version(), group_type or 'all', limit)
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = {
        'group_type': group_type or 'all',
        'updated_at': timezone.localtime().strftime('%Y-%m-%d %H:%M:%S'),
        'top_scorers': top_scorers(group_type, limit),
        'group_goals': group_goals(group_type),
        'team_goals': team_goals(group_type),
    }
    cache.set(key, result, CACHE_TTL)
    return result
