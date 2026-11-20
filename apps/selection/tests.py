"""球员遴选 / 评分 / 最佳球员接口测试。"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, CoachProfile, User, UserRole
from apps.org.models import City, GroupType, Province, School, Team
from apps.registration.models import Competition, Registration

from .models import SelectionBestResult, SelectionProfile, SelectionScore


def token(user):
    return str(RefreshToken.for_user(user).access_token)


class SelectionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='sel-admin@test.com', email='sel-admin@test.com', name='遴选管理员',
            role=UserRole.ADMIN, password='pass1234',
        )
        cls.coach = User.objects.create_user(
            username='13940000001', phone='13940000001', name='遴选教练',
            role=UserRole.COACH, password='pass1234',
        )
        CoachProfile.objects.create(user=cls.coach, name='遴选教练', status='approved')

        province = Province.objects.create(name='遴选区', code='950000')
        city = City.objects.create(province=province, name='遴选市', code='950100')
        school = School.objects.create(city=city, name='遴选学校')
        cls.team = Team.objects.create(school=school, coach=cls.coach, name='遴选一队',
                                       group_type=GroupType.MIDDLE_SCHOOL, status='approved')
        cls.competition = Competition.objects.create(name='遴选杯', year=2026)
        Registration.objects.create(competition=cls.competition, team=cls.team, status='approved')

        cls.athletes = []
        for index in (1, 2):
            user = User.objects.create_user(
                username=f'1394000001{index}', phone=f'1394000001{index}', name=f'遴选球员{index}',
                role=UserRole.ATHLETE, password='pass1234',
            )
            cls.athletes.append(AthleteProfile.objects.create(
                user=user, team=cls.team, name=f'遴选球员{index}', jersey_no=index,
                id_card=f'51010020000101000{index}', status='approved',
            ))

    def setUp(self):
        self.client = APIClient()

    def auth(self, user=None):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token(user or self.admin)}')


class SelectionProfileTest(SelectionTestBase):
    def test_import_profiles_from_registration(self):
        self.auth()
        resp = self.client.post('/api/admin/selection/profiles', {
            'competition_id': self.competition.id,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())
        self.assertEqual(resp.json()['data']['created'], 2)
        self.assertEqual(SelectionProfile.objects.count(), 2)

        # 重复导入不重复建档
        self.client.post('/api/admin/selection/profiles', {'competition_id': self.competition.id},
                         format='json')
        self.assertEqual(SelectionProfile.objects.count(), 2)

    def test_query_by_id_card_and_team(self):
        self.auth()
        self.client.post('/api/admin/selection/profiles', {'competition_id': self.competition.id},
                         format='json')
        data = self.client.get('/api/admin/selection/profiles', {
            'id_card': '510100200001010001',
        }).json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['name'], '遴选球员1')

        data = self.client.get('/api/admin/selection/profiles',
                               {'team_id': self.team.id}).json()['data']
        self.assertEqual(data['total'], 2)

    def test_query_by_id_card_suffix(self):
        self.auth()
        self.client.post('/api/admin/selection/profiles', {'competition_id': self.competition.id},
                         format='json')
        data = self.client.get('/api/admin/selection/profiles', {
            'id_card': '0001',
        }).json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['name'], '遴选球员1')

    def test_non_admin_rejected(self):
        self.auth(self.coach)
        resp = self.client.get('/api/admin/selection/profiles')
        self.assertGreaterEqual(resp.status_code, 403)


class SelectionScoreTest(SelectionTestBase):
    def setUp(self):
        super().setUp()
        self.profiles = [
            SelectionProfile.objects.create(
                competition=self.competition, athlete=athlete, team=self.team,
                group_type=GroupType.MIDDLE_SCHOOL, name_snapshot=athlete.name,
                id_card_snapshot=athlete.id_card, created_by=self.admin.id,
            )
            for athlete in self.athletes
        ]

    def test_weighted_total(self):
        self.auth()
        resp = self.client.post('/api/admin/selection/scores', {
            'profile_id': self.profiles[0].id, 'stage': 'initial',
            'goals_score': 80, 'defense_score': 60, 'assist_score': 70, 'foul_score': 90,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())
        # 80*0.25 + 60*0.25 + 70*0.20 + 90*0.30 = 76.00
        self.assertEqual(resp.json()['data']['total_score'], '76.00')

    def test_update_score_recalcs_total(self):
        self.auth()
        created = self.client.post('/api/admin/selection/scores', {
            'profile_id': self.profiles[0].id, 'stage': 'final',
            'goals_score': 100, 'defense_score': 100, 'assist_score': 100, 'foul_score': 100,
        }, format='json').json()['data']
        resp = self.client.put(f"/api/admin/selection/scores/{created['id']}", {
            'foul_score': 0,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())
        self.assertEqual(resp.json()['data']['total_score'], '70.00')

    def test_summary_and_export(self):
        self.auth()
        for index, profile in enumerate(self.profiles):
            self.client.post('/api/admin/selection/scores', {
                'profile_id': profile.id, 'stage': 'initial',
                'goals_score': 90 - index * 10, 'defense_score': 70,
                'assist_score': 60, 'foul_score': 80,
            }, format='json')

        summary = self.client.get('/api/admin/selection/scores/summary', {
            'competition_id': self.competition.id, 'stage': 'initial',
        }).json()['data']
        self.assertEqual(summary['count'], 2)
        self.assertEqual(summary['teams'][0]['team_id'], self.team.id)

        resp = self.client.get('/api/admin/selection/scores/export', {
            'competition_id': self.competition.id,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])


class SelectionBestTest(SelectionTestBase):
    def setUp(self):
        super().setUp()
        self.profiles = []
        for athlete in self.athletes:
            self.profiles.append(SelectionProfile.objects.create(
                competition=self.competition, athlete=athlete, team=self.team,
                group_type=GroupType.MIDDLE_SCHOOL, name_snapshot=athlete.name,
                id_card_snapshot=athlete.id_card, created_by=self.admin.id,
            ))
        SelectionScore.objects.create(profile=self.profiles[0], stage='final',
                                      goals_score=90, defense_score=90, assist_score=90,
                                      foul_score=90, total_score=90, judge=self.admin)
        SelectionScore.objects.create(profile=self.profiles[1], stage='final',
                                      goals_score=60, defense_score=60, assist_score=60,
                                      foul_score=60, total_score=60, judge=self.admin)

    def test_generate_ranks_by_final_total(self):
        self.auth()
        resp = self.client.post('/api/admin/selection/best/generate', {
            'competition_id': self.competition.id, 'group_type': GroupType.MIDDLE_SCHOOL,
            'top_n': 2,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())
        self.assertEqual(resp.json()['data']['count'], 2)

        rows = SelectionBestResult.objects.order_by('rank')
        self.assertEqual(rows[0].athlete_id, self.athletes[0].id)
        self.assertEqual(rows[0].rank, 1)
        self.assertFalse(rows[0].published)

    def test_publish_visible_in_portal(self):
        self.auth()
        self.client.post('/api/admin/selection/best/generate', {
            'competition_id': self.competition.id, 'group_type': GroupType.MIDDLE_SCHOOL,
        }, format='json')

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token(self.coach)}')
        data = self.client.get('/api/portal/selection-best/', {
            'competition_id': self.competition.id,
        }).json()['data']
        self.assertEqual(data['total'], 0)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token(self.admin)}')
        resp = self.client.post('/api/admin/selection/best/publish', {
            'competition_id': self.competition.id, 'group_type': GroupType.MIDDLE_SCHOOL,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token(self.coach)}')
        data = self.client.get('/api/portal/selection-best/', {
            'competition_id': self.competition.id,
        }).json()['data']
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['list'][0]['rank'], 1)
