"""赛事直播 + 运动员端 + 认证 冒烟测试（Django 版）。

前置：服务已运行在 127.0.0.1:8000，且已完成 migrate / seed_admin。
用法：python scripts/smoke_test.py
"""
import os
import random
import sys
import uuid
from pathlib import Path

import requests

BASE = os.getenv('BASE_URL', 'http://127.0.0.1:8000')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from apps.accounts.models import AthleteProfile, User, UserRole  # noqa: E402
from apps.live.models import Match, MatchStatus  # noqa: E402
from apps.org.models import City, GroupType, School, Team, TeamStatus  # noqa: E402

PASSED = []
FAILED = []


def check(name: str, condition: bool, detail: str = ''):
    if condition:
        PASSED.append(name)
        print(f'  [PASS] {name}')
    else:
        FAILED.append(f'{name} :: {detail}')
        print(f'  [FAIL] {name} :: {detail}')


def api(method: str, path: str, token: str = None, **kwargs):
    headers = kwargs.pop('headers', {})
    if token:
        headers['Authorization'] = f'Bearer {token}'
    response = requests.request(method, f'{BASE}{path}', headers=headers, timeout=15, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = {'raw': response.text[:200]}
    return response.status_code, body


def fixture():
    """构造：学校 / 球队 / 已审核运动员 / 两场比赛。"""
    province = requests.get(f'{BASE}/api/admin/provinces', timeout=5)  # 仅触发服务，不校验
    del province

    city = City.objects.filter(code='510100').first() or City.objects.first()
    school, _ = School.objects.get_or_create(name='成都实验中学', defaults={'city': city})
    coach, _ = User.objects.get_or_create(
        phone='13900000001', defaults={'username': '13900000001', 'name': '李教练', 'role': UserRole.COACH}
    )
    team, _ = Team.objects.get_or_create(
        name='成都实验中学足球队',
        defaults={'school': school, 'coach': coach, 'group_type': GroupType.MIDDLE_SCHOOL, 'status': TeamStatus.APPROVED},
    )
    team.school = school
    team.status = TeamStatus.APPROVED
    team.group_type = GroupType.MIDDLE_SCHOOL
    team.save()

    athlete_user = User.objects.filter(phone='13800000001').first()
    if athlete_user is None:
        athlete_user = User.objects.create_user(username='13800000001', phone='13800000001', name='王球员', role=UserRole.ATHLETE, password='pass1234')
    profile, _ = AthleteProfile.objects.get_or_create(user=athlete_user, defaults={'name': '王球员'})
    profile.team = team
    profile.jersey_no = 10
    profile.position = '中场'
    profile.status = 'approved'
    profile.save()

    opponent, _ = Team.objects.get_or_create(
        name='绵阳南山中学', defaults={'school': school, 'coach': coach, 'group_type': GroupType.MIDDLE_SCHOOL, 'status': TeamStatus.APPROVED}
    )
    opponent.status = TeamStatus.APPROVED
    opponent.save()

    Match.all_objects.all().delete()
    Match.objects.create(
        group_type=GroupType.MIDDLE_SCHOOL,
        home_team_name=team.name, away_team_name=opponent.name,
        home_team=team, away_team=opponent,
        match_time='2026-10-26T15:00:00+08:00', venue='成都体育中心',
        status=MatchStatus.FINISHED, home_score=2, away_score=1,
    )
    Match.objects.create(
        group_type=GroupType.MIDDLE_SCHOOL,
        home_team_name=opponent.name, away_team_name=team.name,
        home_team=opponent, away_team=team,
        match_time='2026-10-28T15:00:00+08:00', venue='绵阳体育中心',
        status=MatchStatus.NOT_STARTED,
    )
    return team, profile


def main():
    print('== 0. 准备数据 ==')
    team, profile = fixture()
    suffix = uuid.uuid4().hex[:6]
    phone = f'139{random.randint(10000000, 99999999)}'

    print('== 1. 认证 ==')
    status, body = api('POST', '/api/auth/register', json={
        'name': f'测试个人{suffix}', 'phone': phone, 'password': 'pass1234', 'role': 'personal'
    })
    check('注册个人账号', status == 200 and body.get('code') == 0, str(body)[:200])
    check('返回 token 与 user', bool(body.get('data', {}).get('token')) and body.get('data', {}).get('user', {}).get('role') == 'personal', str(body)[:200])

    status, body = api('POST', '/api/auth/register', json={
        'name': '重复手机', 'phone': phone, 'password': 'pass1234', 'role': 'personal'
    })
    check('重复手机号被拒(10001)', body.get('code') == 10001, str(body)[:200])

    status, body = api('POST', '/api/auth/register', json={
        'name': '非法手机', 'phone': '12345', 'password': 'pass1234', 'role': 'personal'
    })
    check('非法手机号被拒(10001)', body.get('code') == 10001, str(body)[:200])

    status, body = api('POST', '/api/auth/register', json={
        'name': '非法角色', 'phone': f'137{random.randint(10000000, 99999999)}', 'password': 'pass1234', 'role': 'admin'
    })
    check('自助注册不允许 admin(10001)', body.get('code') == 10001, str(body)[:200])

    coach_phone = f'136{random.randint(10000000, 99999999)}'
    status, body = api('POST', '/api/auth/register', json={
        'name': '新教练', 'phone': coach_phone, 'password': 'pass1234', 'role': 'coach'
    })
    check('注册教练', body.get('code') == 0, str(body)[:200])
    check('教练 profile 待审核', body.get('data', {}).get('user', {}).get('profile', {}).get('status') == 'pending', str(body)[:200])

    status, body = api('POST', '/api/auth/login', json={'phone': phone, 'password': 'pass1234'})
    check('手机号统一登录', body.get('code') == 0 and bool(body.get('data', {}).get('token')), str(body)[:200])
    personal_token = body.get('data', {}).get('token')

    status, body = api('POST', '/api/auth/login', json={'phone': phone, 'password': 'wrong'})
    check('错误密码(20002)', body.get('code') == 20002, str(body)[:200])

    status, body = api('POST', '/api/admin/auth/login', json={'email': 'admin@ball.local', 'password': 'admin123'})
    check('管理员独立登录', body.get('code') == 0 and body.get('data', {}).get('user', {}).get('role') == 'admin', str(body)[:200])
    admin_token = body.get('data', {}).get('token')

    status, body = api('POST', '/api/admin/auth/login', json={'email': 'admin@ball.local', 'password': 'bad'})
    check('管理员错误密码(20002)', body.get('code') == 20002, str(body)[:200])

    status, body = api('GET', '/api/auth/me', token=admin_token)
    check('me 返回管理员信息', body.get('code') == 0 and body.get('data', {}).get('is_admin') is True, str(body)[:200])
    check('响应含 trace_id', bool(body.get('trace_id')), str(body)[:200])

    status, body = api('GET', '/api/auth/me')
    check('未登录访问 me(20001)', body.get('code') == 20001, str(body)[:200])

    status, body = api('POST', '/api/auth/login', json={'phone': 'admin@ball.local', 'password': 'admin123'})
    check('管理员不能走统一登录', body.get('code') in (20002, 20004), str(body)[:200])

    print('== 2. 权限隔离 ==')
    status, body = api('GET', '/api/admin/live/matches', token=personal_token)
    check('个人账号访问管理端(20004)', body.get('code') == 20004, str(body)[:200])

    status, body = api('GET', '/api/athlete/profile', token=personal_token)
    check('个人账号访问运动员端(20004)', body.get('code') == 20004, str(body)[:200])

    print('== 3. 公众端 ==')
    status, body = api('GET', '/api/public/live/matches')
    check('公众比赛列表免登', body.get('code') == 0 and body.get('data', {}).get('total', 0) >= 2, str(body)[:200])
    check('分页含 next_page', 'next_page' in (body.get('data') or {}), str(body)[:200])
    first_row = (body.get('data', {}).get('list') or [{}])[0]
    check('列表字段 *_label', 'group_type_label' in first_row and 'status_label' in first_row, str(first_row)[:200])
    first_id = first_row.get('id')

    status, body = api('GET', f'/api/public/live/matches/{first_id}/events?since_id=999999')
    check('事件 since_id 增量为空', body.get('code') == 0 and body.get('data') == [], str(body)[:200])

    status, body = api('GET', f'/api/public/live/matches/{first_id}')
    check('公众比赛详情', body.get('code') == 0 and 'referees' in body.get('data', {}), str(body)[:200])

    print('== 4. 管理端比赛 ==')
    status, body = api('GET', '/api/admin/live/matches', token=admin_token)
    check('管理端比赛列表', body.get('code') == 0, str(body)[:200])

    status, body = api('POST', '/api/admin/live/matches', token=admin_token, json={
        'group_type': 'middle_school', 'home_team_name': 'A队', 'away_team_name': 'B队',
        'match_time': '2026-11-01T15:00:00+08:00', 'venue': '测试场地',
    })
    check('创建比赛', body.get('code') == 0, str(body)[:200])
    match_id = body.get('data', {}).get('id')
    version = body.get('data', {}).get('version')

    status, body = api('PUT', f'/api/admin/live/matches/{match_id}', token=admin_token, json={
        'version': 999, 'venue': '改场地',
    })
    check('乐观锁冲突(code=9)', body.get('code') == 9, str(body)[:200])

    status, body = api('PUT', f'/api/admin/live/matches/{match_id}', token=admin_token, json={
        'version': version, 'venue': '改后场地',
    })
    check('正确版本更新成功', body.get('code') == 0 and body.get('data', {}).get('venue') == '改后场地', str(body)[:200])
    version = body.get('data', {}).get('version')

    print('== 5. 事件与比分联动 ==')
    token_value = uuid.uuid4().hex
    status, body = api('POST', f'/api/admin/live/matches/{match_id}/events', token=admin_token, json={
        'type': 'goal', 'minute': 12, 'team_side': 'home', 'player_name': '王球员',
        'player_no': '10', 'client_token': token_value, 'athlete_id': profile.id,
    })
    check('新增进球事件', body.get('code') == 0, str(body)[:200])
    event_id = (body.get('data', {}).get('event') or {}).get('id')
    check('事件回包含最新比分/版本', {
        'home_score', 'away_score', 'version', 'event',
    } <= set(body.get('data') or {}), str(body)[:200])
    check('事件比分已 +1', body.get('data', {}).get('home_score') == 1, str(body)[:200])

    status, body = api('GET', f'/api/admin/live/matches/{match_id}', token=admin_token)
    check('进球后主队比分 +1', body.get('data', {}).get('home_score') == 1, str(body)[:200])

    status, body = api('POST', f'/api/admin/live/matches/{match_id}/events', token=admin_token, json={
        'type': 'goal', 'minute': 13, 'team_side': 'home', 'player_name': '王球员',
        'player_no': '10', 'client_token': token_value,
    })
    check('重复 client_token(40009)', body.get('code') == 40009, str(body)[:200])

    status, body = api('POST', f'/api/admin/live/matches/{match_id}/events', token=admin_token, json={
        'type': 'text', 'minute': 15, 'client_token': uuid.uuid4().hex, 'content': '现场气氛热烈',
    })
    check('文字事件无需主客队', body.get('code') == 0, str(body)[:200])

    status, body = api('POST', f'/api/admin/live/matches/{match_id}/events', token=admin_token, json={
        'type': 'substitution', 'minute': 20, 'team_side': 'home', 'player_name': '王球员',
        'client_token': uuid.uuid4().hex,
    })
    check('换人缺换上球员(10001)', body.get('code') == 10001, str(body)[:200])

    status, body = api('DELETE', f'/api/admin/live/events/{event_id}', token=admin_token)
    check('删除进球事件', body.get('code') == 0, str(body)[:200])

    status, body = api('GET', f'/api/admin/live/matches/{match_id}', token=admin_token)
    check('删除后比分回退', body.get('data', {}).get('home_score') == 0, str(body)[:200])

    print('== 6. 裁判 / 阵容 / 直播链接 ==')
    status, body = api('PUT', f'/api/admin/live/matches/{match_id}/referees', token=admin_token, json={
        'referees': [{'role': 'chief', 'name': '张主裁'}, {'role': 'assistant', 'name': '李边裁'}]
    })
    check('替换裁判成功', body.get('code') == 0 and len(body.get('data') or []) == 2, str(body)[:200])
    check('裁判字段 role_label', 'role_label' in (body.get('data') or [{}])[0], str(body)[:200])

    status, body = api('PUT', f'/api/admin/live/matches/{match_id}/referees', token=admin_token, json={
        'referees': [{'role': 'chief', 'name': 'A'}, {'role': 'chief', 'name': 'B'}]
    })
    check('主裁超限(10001)', body.get('code') == 10001, str(body)[:200])

    status, body = api('GET', f'/api/admin/live/matches/{match_id}', token=admin_token)
    version = body.get('data', {}).get('version')
    status, body = api('PUT', f'/api/admin/live/matches/{match_id}/lineup', token=admin_token, json={
        'version': version,
        'lineup_home': [{'name': '王球员', 'no': '10', 'position': '中场', 'is_captain': True}],
        'lineup_away': [{'name': '赵球员', 'no': '7'}],
    })
    check('更新阵容', body.get('code') == 0 and len(body.get('data', {}).get('lineup_home') or []) == 1, str(body)[:200])

    status, body = api('PUT', f'/api/admin/live/matches/{match_id}/live-link', token=admin_token, json={
        'live_link': 'https://live.example.com/room/1'
    })
    check('设置直播链接', body.get('code') == 0 and body.get('data', {}).get('live_link', '').endswith('/room/1'), str(body)[:200])

    print('== 7. 状态流转 ==')
    status, body = api('GET', f'/api/admin/live/matches/{match_id}', token=admin_token)
    version = body.get('data', {}).get('version')
    status, body = api('PATCH', f'/api/admin/live/matches/{match_id}/status', token=admin_token, json={
        'action': 'start', 'version': version
    })
    check('未开始→进行中', body.get('code') == 0 and body.get('data', {}).get('status') == 'live', str(body)[:200])
    version = body.get('data', {}).get('version')

    status, body = api('PATCH', f'/api/admin/live/matches/{match_id}/status', token=admin_token, json={
        'action': 'finish', 'version': version
    })
    check('进行中→已结束', body.get('code') == 0 and body.get('data', {}).get('status') == 'finished', str(body)[:200])
    version = body.get('data', {}).get('version')

    status, body = api('PUT', f'/api/admin/live/matches/{match_id}', token=admin_token, json={
        'version': version, 'venue': '赛后改场地'
    })
    check('已结束禁止修改(40002)', body.get('code') == 40002, str(body)[:200])

    status, body = api('POST', f'/api/admin/live/matches/{match_id}/events', token=admin_token, json={
        'type': 'goal', 'minute': 90, 'team_side': 'home', 'player_name': '王球员',
        'client_token': uuid.uuid4().hex,
    })
    check('已结束禁止记事件(40002)', body.get('code') == 40002, str(body)[:200])

    status, body = api('PATCH', f'/api/admin/live/matches/{match_id}/status', token=admin_token, json={
        'action': 'start', 'version': version
    })
    check('已结束不能再开始(40002)', body.get('code') == 40002, str(body)[:200])

    print('== 8. 文件上传 ==')
    status, body = api('POST', f'/api/admin/live/matches/{match_id}/files', token=admin_token,
                       files={'file': ('签到表.txt', b'hello', 'text/plain')}, data={'type': 'sign_in'})
    check('非法格式(10006)', body.get('code') == 10006, str(body)[:200])

    status, body = api('POST', f'/api/admin/live/matches/{match_id}/files', token=admin_token,
                       files={'file': ('签到表.pdf', b'%PDF-1.4 test', 'application/pdf')}, data={'type': 'sign_in'})
    check('上传 PDF 成功', body.get('code') == 0, str(body)[:200])
    file_id = body.get('data', {}).get('id')

    response = requests.get(f'{BASE}/api/admin/live/files/{file_id}/download',
                            headers={'Authorization': f'Bearer {admin_token}'}, timeout=10)
    check('下载文件', response.status_code == 200 and response.content.startswith(b'%PDF'), f'status={response.status_code}')

    status, body = api('GET', f'/api/admin/live/matches/{match_id}/files', token=admin_token)
    check('文件列表', body.get('code') == 0 and len(body.get('data') or []) == 1, str(body)[:200])

    print('== 9. 运动员端 ==')
    status, body = api('POST', '/api/auth/login', json={'phone': '13800000001', 'password': 'pass1234'})
    athlete_token = body.get('data', {}).get('token')
    check('运动员登录', bool(athlete_token), str(body)[:200])

    status, body = api('GET', '/api/athlete/profile', token=athlete_token)
    check('运动员资料', body.get('code') == 0 and body.get('data', {}).get('team', {}).get('id') == team.id, str(body)[:200])

    status, body = api('PUT', '/api/athlete/profile', token=athlete_token, json={'jersey_no': 9, 'position': '前锋'})
    check('更新运动员资料', body.get('code') == 0 and body.get('data', {}).get('jersey_no') == 9, str(body)[:200])

    status, body = api('GET', '/api/athlete/matches', token=athlete_token)
    check('我的比赛列表', body.get('code') == 0 and len(body.get('data', {}).get('list') or []) == 2, str(body)[:200])
    home_flags = body.get('data', {}).get('list') or []
    check('主客队识别', {row['is_home'] for row in home_flags} == {True, False}, str(home_flags)[:200])

    status, body = api('GET', '/api/athlete/matches?status=finished', token=athlete_token)
    check('按状态筛选', len(body.get('data', {}).get('list') or []) == 1, str(body)[:200])

    status, body = api('GET', '/api/athlete/stats', token=athlete_token)
    data = body.get('data') or {}
    check('数据统计', body.get('code') == 0 and data.get('team_record', {}).get('win') == 1, str(body)[:200])

    status, body = api('GET', '/api/athlete/standings', token=athlete_token)
    data = body.get('data') or {}
    check('积分榜', body.get('code') == 0 and len(data.get('standings') or []) >= 2, str(body)[:200])
    mine = [row for row in data.get('standings') or [] if row.get('is_my_team')]
    check('我方标注与积分', bool(mine) and mine[0]['points'] == 3, str(data.get('standings'))[:200])

    print('== 10. 组织端 ==')
    status, body = api('GET', '/api/admin/provinces', token=admin_token)
    check('省份列表', body.get('code') == 0 and body.get('data', {}).get('total') == 1, str(body)[:200])

    status, body = api('GET', '/api/admin/cities?province_id=1', token=admin_token)
    check('城市列表(21)', body.get('code') == 0 and body.get('data', {}).get('total') == 21, str(body)[:200])

    status, body = api('GET', '/api/admin/schools', token=admin_token)
    check('学校列表', body.get('code') == 0, str(body)[:200])

    status, body = api('GET', '/api/admin/provinces', token=personal_token)
    check('个人不能访问管理端组织接口(20004)', body.get('code') == 20004, str(body)[:200])

    print('== 11. 登出 ==')
    status, body = api('POST', '/api/auth/logout', token=admin_token)
    check('登出', body.get('code') == 0, str(body)[:200])

    print('\n' + '=' * 60)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('\n失败明细：')
        for item in FAILED:
            print(f'  - {item}')
        sys.exit(1)


if __name__ == '__main__':
    main()
