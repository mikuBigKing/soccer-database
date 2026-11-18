"""报名流程、快照与统计口径回归测试。"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import (
    AthleteProfile, CityProfile, CoachProfile, ProvinceProfile, SchoolProfile, UserRole,
)
from apps.live.models import EventType, Match, MatchEvent, MatchStatus
from apps.org.models import City, GroupType, Province, School, Team, TeamStatus
from apps.registration.models import Competition, Registration

User = get_user_model()


def access_token(user):
    return str(RefreshToken.for_user(user).access_token)


COMPLETE = {'complete': True, 'items': [], 'missing': []}


class RegistrationFlowTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='940000')
        self.city = City.objects.create(province=self.province, name='测试市', code='940100')
        self.school = School.objects.create(city=self.city, name='测试学校')
        self.coach = User.objects.create_user(
            username='13930000001', phone='13930000001', name='教练',
            role=UserRole.COACH, password='pass1234',
        )
        CoachProfile.objects.create(user=self.coach, name='教练', status='approved', qualification_path='x.pdf')
        self.team = Team.objects.create(
            school=self.school, coach=self.coach, name='测试队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.competition = Competition.objects.create(name='联赛', year=2026)
        self.alt_competition = Competition.objects.create(name='杯赛', year=2026)
        self.athlete = User.objects.create_user(
            username='13930000002', phone='13930000002', name='球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        AthleteProfile.objects.create(
            user=self.athlete, name='球员', team=self.team, jersey_no=10,
            status='approved', id_card='510100200001011234', enrollment_cert_path='certs/a.pdf',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(self.coach)}')

    def _registration(self, **kwargs):
        defaults = {'competition': self.competition, 'team': self.team}
        defaults.update(kwargs)
        return Registration.objects.create(**defaults)

    def test_pending_team_cannot_register(self):
        self.team.status = TeamStatus.PENDING
        self.team.save(update_fields=['status'])
        resp = self.client.post('/api/registrations', {
            'competition_id': self.competition.pk, 'team_id': self.team.pk,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('审核', resp.json()['msg'])

    def test_competition_group_type_restriction(self):
        self.competition.group_types = [GroupType.PRIMARY_SCHOOL]
        self.competition.save(update_fields=['group_types'])
        resp = self.client.post('/api/registrations', {
            'competition_id': self.competition.pk, 'team_id': self.team.pk,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

    def test_competition_sign_up_window(self):
        self.competition.sign_up_start_at = timezone.now() + timedelta(days=1)
        self.competition.save(update_fields=['sign_up_start_at'])
        resp = self.client.post('/api/registrations', {
            'competition_id': self.competition.pk, 'team_id': self.team.pk,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('尚未开始', resp.json()['msg'])

    def test_submit_creates_snapshot_and_clears_review_metadata(self):
        registration = self._registration(
            status=Registration.Status.REJECTED,
            reject_level=Registration.RejectLevel.CITY,
            reject_reason='资料不全',
            city_reviewed_by=self.coach.pk,
            city_reviewed_at=timezone.now(),
        )
        with patch('apps.registration.services.check_completeness', return_value=COMPLETE):
            resp = self.client.post(f'/api/registrations/{registration.pk}/submit')
        self.assertEqual(resp.json()['code'], 0)
        registration.refresh_from_db()
        self.assertEqual(len(registration.players_snapshot), 1)
        self.assertEqual(registration.players_snapshot[0]['name'], '球员')
        self.assertEqual(registration.coach_snapshot['name'], '教练')
        self.assertIsNone(registration.city_reviewed_by)
        self.assertIsNone(registration.city_reviewed_at)
        self.assertIsNone(registration.reject_reason)

    def test_detail_uses_snapshot_after_submit(self):
        registration = self._registration(status=Registration.Status.SUBMITTED)
        registration.players_snapshot = [{
            'id': 1, 'user_id': 1, 'name': '历史球员', 'jersey_no': 9,
            'position': '前锋', 'id_card': '510***********1234',
            'has_enrollment_cert': True, 'has_avatar': False,
            'enrollment_cert_name': None, 'avatar_name': None,
            'status': 'approved', 'reject_reason': None,
        }]
        registration.coach_snapshot = {'id': 1, 'name': '历史教练'}
        registration.save(update_fields=['players_snapshot', 'coach_snapshot'])

        # 报名后球队名单发生变化，不应影响历史报名详情
        second = User.objects.create_user(
            username='13930000009', phone='13930000009', name='新球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        AthleteProfile.objects.create(user=second, name='新球员', team=self.team, jersey_no=11)

        resp = self.client.get(f'/api/registrations/{registration.pk}')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(len(resp.json()['data']['players']), 1)
        self.assertEqual(resp.json()['data']['players'][0]['name'], '历史球员')
        self.assertEqual(resp.json()['data']['coach']['name'], '历史教练')
        self.assertEqual(resp.json()['data']['player_count'], 1)

    def test_update_and_delete_draft_registration(self):
        registration = self._registration()
        resp = self.client.put(
            f'/api/registrations/{registration.pk}',
            {'competition_id': self.alt_competition.pk},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        registration.refresh_from_db()
        self.assertEqual(registration.competition_id, self.alt_competition.pk)

        deleted = self.client.delete(f'/api/registrations/{registration.pk}')
        self.assertEqual(deleted.json()['code'], 0)
        self.assertFalse(Registration.objects.filter(pk=registration.pk).exists())

    def test_cannot_delete_submitted_registration(self):
        registration = self._registration(status=Registration.Status.SUBMITTED)
        resp = self.client.delete(f'/api/registrations/{registration.pk}')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertTrue(Registration.objects.filter(pk=registration.pk).exists())

    def test_submitter_can_download_form_docx(self):
        registration = self._registration(status=Registration.Status.SUBMITTED)
        resp = self.client.get(f'/api/registrations/{registration.pk}/form-docx')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('wordprocessingml', resp['Content-Type'])
        self.assertGreater(len(resp.content), 0)

    def test_report_clears_previous_province_review_metadata(self):
        registration = self._registration(
            status=Registration.Status.CITY_APPROVED,
            province_reviewed_by=1,
            province_reviewed_at=timezone.now(),
        )
        city_user = User.objects.create_user(
            username='13930000003', phone='13930000003', name='市级',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=city_user, name='市级', city=self.city)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(city_user)}')
        resp = self.client.post(f'/api/city/registrations/{registration.pk}/report')
        self.assertEqual(resp.json()['code'], 0)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.REPORTED)
        self.assertIsNone(registration.province_reviewed_by)
        self.assertIsNone(registration.province_reviewed_at)

    def test_pagination_returns_actual_page(self):
        for index in range(3):
            Competition.objects.create(name=f'赛事{index}', year=2025)
        resp = self.client.get('/api/competitions?page=999&page_size=1')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['total'], 5)
        self.assertEqual(resp.json()['data']['page'], 5)

    def test_duplicate_jersey_rejected_for_athlete_update(self):
        other = User.objects.create_user(
            username='13930000004', phone='13930000004', name='队友',
            role=UserRole.ATHLETE, password='pass1234',
        )
        AthleteProfile.objects.create(user=other, name='队友', team=self.team, jersey_no=7, status='approved')
        athlete_client = APIClient()
        athlete_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(self.athlete)}')
        resp = athlete_client.put('/api/athlete/profile', {'jersey_no': 7}, format='json')
        self.assertEqual(resp.json()['code'], 10001)

    def test_athlete_can_fill_id_card_for_verification(self):
        new_user = User.objects.create_user(
            username='13930000007', phone='13930000007', name='新球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        profile = AthleteProfile.objects.create(user=new_user, name='新球员', status='pending')
        athlete_client = APIClient()
        athlete_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(new_user)}')
        resp = athlete_client.put(
            '/api/athlete/profile', {'id_card': '510100200007077890'}, format='json'
        )
        self.assertEqual(resp.json()['code'], 0)
        profile.refresh_from_db()
        self.assertEqual(profile.id_card, '510100200007077890')

    def test_duplicate_id_card_rejected(self):
        other = User.objects.create_user(
            username='13930000005', phone='13930000005', name='队友',
            role=UserRole.ATHLETE, password='pass1234',
        )
        other_profile = AthleteProfile.objects.create(
            user=other, name='队友', team=self.team, jersey_no=8,
            status='approved', id_card='510100200002022345',
        )
        province_user = User.objects.create_user(
            username='13930000006', phone='13930000006', name='省级',
            role=UserRole.PROVINCE, password='pass1234',
        )
        ProvinceProfile.objects.create(user=province_user, name='省级', province=self.province)
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(province_user)}')
        resp = admin_client.put(
            f'/api/province/athletes/{other_profile.pk}',
            {'id_card': '510100200001011234'},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)


class RegistrationStatsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='950000')
        self.city = City.objects.create(province=self.province, name='测试市', code='950100')
        self.school = School.objects.create(city=self.city, name='测试学校')
        self.coach = User.objects.create_user(
            username='13940000001', phone='13940000001', name='教练',
            role=UserRole.COACH, password='pass1234',
        )
        CoachProfile.objects.create(user=self.coach, name='教练', status='approved')
        self.team = Team.objects.create(
            school=self.school, coach=self.coach, name='甲队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.team_b = Team.objects.create(
            school=self.school, coach=self.coach, name='乙队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.athlete = User.objects.create_user(
            username='13940000002', phone='13940000002', name='射手',
            role=UserRole.ATHLETE, password='pass1234',
        )
        self.profile = AthleteProfile.objects.create(
            user=self.athlete, name='射手', team=self.team, jersey_no=10,
            status='approved', id_card='510100200003033456',
        )
        self.comp_new = Competition.objects.create(name='2026联赛', year=2026)
        self.comp_old = Competition.objects.create(name='2025杯赛', year=2025)
        for competition in (self.comp_new, self.comp_old):
            Registration.objects.create(competition=competition, team=self.team, status=Registration.Status.APPROVED)
            Registration.objects.create(competition=competition, team=self.team_b, status=Registration.Status.APPROVED)

        self.match_new = Match.objects.create(
            competition=self.comp_new, group_type=GroupType.MIDDLE_SCHOOL,
            home_team_name='甲队', away_team_name='乙队', home_team=self.team, away_team=self.team_b,
            match_time='2026-11-01T15:00:00+08:00', status=MatchStatus.FINISHED,
            home_score=2, away_score=1,
        )
        self.match_old = Match.objects.create(
            competition=self.comp_old, group_type=GroupType.MIDDLE_SCHOOL,
            home_team_name='甲队', away_team_name='乙队', home_team=self.team, away_team=self.team_b,
            match_time='2025-11-01T15:00:00+08:00', status=MatchStatus.FINISHED,
            home_score=5, away_score=0,
        )
        for match, count in ((self.match_new, 1), (self.match_old, 2)):
            for _ in range(count):
                MatchEvent.objects.create(
                    match=match, type=EventType.GOAL, minute=10, team_side='home',
                    athlete=self.profile, client_token=__import__('uuid').uuid4().hex,
                )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(self.athlete)}')

    def test_standings_default_to_latest_registered_competition(self):
        resp = self.client.get('/api/athlete/standings')
        data = resp.json()['data']
        self.assertEqual(data['competition_id'], self.comp_new.pk)
        mine = [row for row in data['standings'] if row['is_my_team']][0]
        self.assertEqual(mine['played'], 1)
        self.assertEqual(mine['gf'], 2)

    def test_standings_explicit_competition(self):
        resp = self.client.get(f'/api/athlete/standings?competition_id={self.comp_old.pk}')
        data = resp.json()['data']
        self.assertEqual(data['competition_id'], self.comp_old.pk)
        mine = [row for row in data['standings'] if row['is_my_team']][0]
        self.assertEqual(mine['gf'], 5)

    def test_stats_default_scope_excludes_other_competitions(self):
        resp = self.client.get('/api/athlete/stats')
        self.assertEqual(resp.json()['data']['goals'], 1)
        self.assertEqual(resp.json()['data']['competition_id'], self.comp_new.pk)

    def test_stats_explicit_competition(self):
        resp = self.client.get(f'/api/athlete/stats?competition_id={self.comp_old.pk}')
        self.assertEqual(resp.json()['data']['goals'], 2)

    def test_province_statistics_uses_friendly_keys(self):
        province_user = User.objects.create_user(
            username='13940000003', phone='13940000003', name='省级',
            role=UserRole.PROVINCE, password='pass1234',
        )
        ProvinceProfile.objects.create(user=province_user, name='省级', province=self.province)
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(province_user)}')
        resp = admin_client.get('/api/province/registrations/statistics')
        by_city = resp.json()['data']['by_city']
        self.assertEqual(by_city[0]['city_id'], self.city.pk)
        self.assertEqual(by_city[0]['city_name'], '测试市')
        by_group = resp.json()['data']['by_group']
        self.assertEqual(by_group[0]['group_type'], 'middle_school')
        self.assertEqual(by_group[0]['group_type_label'], '中学组')

    def test_city_summary_uses_friendly_keys(self):
        city_user = User.objects.create_user(
            username='13940000004', phone='13940000004', name='市级',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=city_user, name='市级', city=self.city)
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(city_user)}')
        resp = admin_client.get('/api/city/registrations/summary')
        self.assertEqual(resp.json()['data']['by_school'][0]['school_name'], '测试学校')
        self.assertEqual(resp.json()['data']['by_group'][0]['group_type_label'], '中学组')

