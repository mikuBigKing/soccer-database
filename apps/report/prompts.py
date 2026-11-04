"""赛事报告 Prompt：SYSTEM 角色设定 + USER 数据模板。

参考 PitchIQ story agent 的角色设定，补充中文与反幻觉约束。
"""
PROMPT_VERSION = 'v2'

SYSTEM = """你是校园足球赛事的中文比赛报告撰写者，面向赛事门户的普通球迷读者。
只依据用户提供的比赛数据写作，禁止编造未给出的比分、球员、时间、战术或裁判信息。
若数据不足（例如缺少某队事件、缺少阵容），在 data_notes 中如实说明。
不得推断数据未给出的因果关系，例如未给出两张黄牌时不得写“两黄变一红”，未给出换人时不得写“战术调整收到奇效”。
输出必须是单个 JSON 对象，不要 markdown 代码块，不要额外解释文字。
文风：客观、有画面感，每章 100-200 字。"""

OUTPUT_SCHEMA = """{
  "narrative": "全文，300-600 字",
  "chapters": [{"title": "章节标题", "content": "章节内容", "minute_range": "如 1-45", "emotion": "如 紧张"}],
  "key_moments": [{"minute": 12, "type": "goal|yellow|red|substitution|text", "description": "", "impact": ""}],
  "highlights": ["一句话高光点"],
  "data_notes": ["数据不足时的说明，没有则给空数组"],
  "confidence": 82
}
confidence 为 0-100 的整数，按数据完整度给出（字段齐全、事件完整时 80 以上；缺阵容或单边数据不足时相应降低），不要填 0。"""

BUCKET_LABELS = [
    ('0-15', 0, 15),
    ('16-30', 16, 30),
    ('31-45', 31, 45),
    ('46-60', 46, 60),
    ('61-75', 61, 75),
    ('76-90', 76, 90),
]

_EVENT_LABELS = {
    'goal': '进球',
    'yellow': '黄牌',
    'red': '红牌',
    'substitution': '换人',
    'text': '文字',
}


def event_label(event_type: str) -> str:
    return _EVENT_LABELS.get(event_type, event_type)


def _lineup_text(players) -> str:
    if not players:
        return '（未提供）'
    parts = []
    for p in players:
        no = p.get('no') or p.get('player_no') or ''
        name = p.get('name') or ''
        pos = p.get('position') or ''
        captain = '，队长' if p.get('is_captain') else ''
        parts.append(f'{no} {name}({pos}{captain})'.strip())
    return ' / '.join(parts)


def _bucket_text(buckets: dict) -> str:
    return '，'.join(f'{label} {buckets.get(label, 0)}' for label, _, _ in BUCKET_LABELS)


def _score_changes_text(changes) -> str:
    if not changes:
        return '无'
    return ' → '.join(f"{c['minute']}' {c['home']}-{c['away']}" for c in changes)


def build_user(payload: dict) -> str:
    """把服务层组装好的结构化数据渲染成紧凑文本，减少 token 消耗。"""
    m = payload['match']
    referees = payload.get('referees') or {}
    derived = payload.get('derived') or {}
    events = payload.get('events') or []
    lineups = payload.get('lineups') or {}
    data_quality = derived.get('data_quality') or {}

    lines = [
        f"【比赛】{m.get('group_type_label') or ''} | 主队 {m.get('home_team')} {m.get('home_score')} - "
        f"{m.get('away_score')} 客队 {m.get('away_team')} | {m.get('match_time') or ''} | 场地：{m.get('venue') or '未提供'}",
        f"【裁判】主裁：{'、'.join(referees.get('chief') or ['未提供'])}；"
        f"边裁：{'、'.join(referees.get('assistant') or ['未提供'])}；"
        f"第四官员：{'、'.join(referees.get('fourth') or ['未提供'])}",
        '【事件时间线】',
    ]

    if events:
        for e in events:
            text = f"{e['minute']}' {e.get('type_label') or event_label(e.get('type'))}"
            if e.get('team'):
                text += f" {e['team']}"
            if e.get('player_no') or e.get('player_name'):
                text += f" {e.get('player_no') or ''}号 {e.get('player_name') or ''}".rstrip()
            if e.get('related'):
                text += f"（{e['related']}）"
            if e.get('content'):
                text += f" {e['content']}"
            lines.append(text)
    else:
        lines.append('（无事件）')

    lines.append(f"【主队阵容】{_lineup_text(lineups.get('home'))}")
    lines.append(f"【客队阵容】{_lineup_text(lineups.get('away'))}")
    lines.append('【派生统计】')
    lines.append(
        f"进球：主 {derived.get('goals', {}).get('home', 0)} / 客 {derived.get('goals', {}).get('away', 0)}；"
        f"黄牌：主 {derived.get('yellow', {}).get('home', 0)} / 客 {derived.get('yellow', {}).get('away', 0)}；"
        f"红牌：主 {derived.get('red', {}).get('home', 0)} / 客 {derived.get('red', {}).get('away', 0)}；"
        f"换人：主 {derived.get('substitution', {}).get('home', 0)} / 客 {derived.get('substitution', {}).get('away', 0)}"
    )
    lines.append(f"进球时段分布：{_bucket_text(derived.get('goal_buckets') or {})}")
    lines.append(f"比分变化：{_score_changes_text(derived.get('score_changes') or [])}")
    lines.append(f"先进球方：{derived.get('first_scorer_label') or '无'}；是否出现红牌：{'是' if derived.get('has_red') else '否'}")
    if data_quality.get('has_lineup') is False:
        lines.append('数据说明：本场未提供阵容。')
    if data_quality.get('has_home_events') is False or data_quality.get('has_away_events') is False:
        lines.append('数据说明：某一方缺少事件记录，叙事需谨慎。')

    lines.append(f'【输出要求】严格返回该 JSON 结构：{OUTPUT_SCHEMA}')
    return '\n'.join(lines)
