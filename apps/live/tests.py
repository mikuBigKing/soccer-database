"""赛事直播模块回归测试。"""
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, User, UserRole
from apps.live.models import Match, MatchEvent, MatchFile, MatchStatus
from apps.org.models import City, GroupType, Province, School, Team, TeamStatus
from apps.registration.models import Competition


def access_token(user):
    return str(RefreshToken.for_user(user).access_token)


class LiveGuardTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='920000')
        self.city = City.objects.create(province=self.province, name='测试市', code='920100')
        self.school = School.objects.create(city=self.city, name='测试学校')
        self.admin = User.objects.create_user(
            username='live-admin@test.com', email='live-admin@test.com', name='管理员',
            role=UserRole.ADMIN, password='pass1234',
        )
        self.coach = User.objects.create_user(
            username='13910000001', phone='13910000001', name='教练',
            role=UserRole.COACH, password='pass1234',
        )
        self.team_a = Team.objects.create(
            school=self.school, coach=self.coach, name='A队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.team_b = Team.objects.create(
            school=self.school, coach=self.coach, name='B队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.competition = Competition.objects.create(name='联赛', year=2026)
        self.match = Match.objects.create(
            competition=self.competition, group_type=GroupType.MIDDLE_SCHOOL,
            home_team_name='A队', away_team_name='B队',
            home_team=self.team_a, away_team=self.team_b,
            match_time='2026-11-01T15:00:00+08:00',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(self.admin)}')

    def test_create_match_with_nonexistent_team_returns_10001(self):
        resp = self.client.post('/api/admin/live/matches', {
            'group_type': 'middle_school', 'home_team_name': 'A', 'away_team_name': 'B',
            'match_time': '2026-11-01T15:00:00+08:00', 'home_team_id': 999999,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)

    def test_create_match_with_nonexistent_competition_returns_10001(self):
        resp = self.client.post('/api/admin/live/matches', {
            'group_type': 'middle_school', 'home_team_name': 'A', 'away_team_name': 'B',
            'match_time': '2026-11-01T15:00:00+08:00', 'competition_id': 999999,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

    def test_update_requires_version(self):
        resp = self.client.put(
            f'/api/admin/live/matches/{self.match.pk}', {'venue': '新场地'}, format='json'
        )
        self.assertEqual(resp.json()['code'], 10001)

    def test_update_refreshes_updated_at(self):
        old = self.match.updated_at
        resp = self.client.put(
            f'/api/admin/live/matches/{self.match.pk}',
            {'version': 0, 'venue': '新场地'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.match.refresh_from_db()
        self.assertGreater(self.match.updated_at, old)

    def test_event_player_no_must_be_numeric(self):
        resp = self.client.post(f'/api/admin/live/matches/{self.match.pk}/events', {
            'type': 'goal', 'minute': 10, 'team_side': 'home', 'player_name': '王',
            'player_no': 'abc', 'client_token': uuid.uuid4().hex,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

    def test_event_invalid_athlete_returns_10001(self):
        resp = self.client.post(f'/api/admin/live/matches/{self.match.pk}/events', {
            'type': 'goal', 'minute': 10, 'team_side': 'home', 'player_name': '王',
            'client_token': uuid.uuid4().hex, 'athlete_id': 999999,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

    def test_event_partial_update_validates_final_state(self):
        event = MatchEvent.objects.create(
            match=self.match, type='text', minute=10, content='现场', client_token=uuid.uuid4().hex,
        )
        resp = self.client.put(f'/api/admin/live/events/{event.pk}', {'type': 'goal'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)

    def test_event_update_cannot_change_client_token(self):
        token_value = uuid.uuid4().hex
        event = MatchEvent.objects.create(
            match=self.match, type='text', minute=10, content='现场', client_token=token_value,
        )
        resp = self.client.put(
            f'/api/admin/live/events/{event.pk}',
            {'content': '更新', 'client_token': 'hacked-token'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        event.refresh_from_db()
        self.assertEqual(event.client_token, token_value)

    def test_duplicate_referee_returns_10001(self):
        resp = self.client.put(f'/api/admin/live/matches/{self.match.pk}/referees', {
            'referees': [{'role': 'assistant', 'name': '张'}, {'role': 'assistant', 'name': '张'}],
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)

    def test_finished_match_locks_referees_and_files(self):
        self.match.status = MatchStatus.FINISHED
        self.match.save(update_fields=['status'])

        referee = self.client.put(f'/api/admin/live/matches/{self.match.pk}/referees', {
            'referees': [{'role': 'chief', 'name': '主裁'}],
        }, format='json')
        self.assertEqual(referee.json()['code'], 40002)

        upload = self.client.post(
            f'/api/admin/live/matches/{self.match.pk}/files',
            {'type': 'sign_in', 'file': SimpleUploadedFile('a.pdf', b'%PDF-1.4', content_type='application/pdf')},
            format='multipart',
        )
        self.assertEqual(upload.json()['code'], 40002)

    def test_finished_match_locks_event_changes(self):
        self.match.status = MatchStatus.FINISHED
        self.match.save(update_fields=['status'])
        resp = self.client.post(f'/api/admin/live/matches/{self.match.pk}/events', {
            'type': 'goal', 'minute': 90, 'team_side': 'home', 'player_name': '王',
            'client_token': uuid.uuid4().hex,
        }, format='json')
        self.assertEqual(resp.json()['code'], 40002)

    def test_match_list_filters_by_competition(self):
        other = Competition.objects.create(name='杯赛', year=2025)
        Match.objects.create(
            competition=other, group_type=GroupType.MIDDLE_SCHOOL,
            home_team_name='C', away_team_name='D', match_time='2026-11-02T15:00:00+08:00',
        )
        resp = self.client.get(f'/api/admin/live/matches?competition_id={self.competition.pk}')
        self.assertEqual(resp.json()['data']['total'], 1)
        self.assertEqual(resp.json()['data']['list'][0]['competition_id'], self.competition.pk)

    def test_file_delete_logs_file_id(self):
        record = MatchFile.objects.create(
            match=self.match, type='sign_in', file_name='a.pdf',
            file_path='matches/999/not-exists.pdf', mime='application/pdf', size=1,
        )
        with self.assertLogs('business', level='INFO') as logs:
            resp = self.client.delete(f'/api/admin/live/files/{record.pk}')
        self.assertEqual(resp.json()['code'], 0)
        self.assertTrue(any(f'file_id={record.pk}' in line for line in logs.output))

    def test_admin_endpoint_requires_login(self):
        self.client.credentials()
        resp = self.client.get('/api/admin/live/matches')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['code'], 20001)

    def test_competition_name_in_match_list(self):
        resp = self.client.get('/api/admin/live/matches')
        self.assertEqual(resp.json()['data']['list'][0]['competition_name'], '联赛')

