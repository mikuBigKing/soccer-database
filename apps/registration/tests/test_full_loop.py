"""P0-03 全链路实测：注册 → 校级实名审核 → 建队 → 球队审核 → 加球员 → 报名提交。"""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AthleteProfile, CoachProfile, SchoolProfile, UserRole
from apps.org.models import City, GroupType, Province, School, Team
from apps.registration.models import Competition, Registration


class FullRegistrationLoopTest(TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix='soccer-loop-')
        self._override = override_settings(PRIVATE_STORAGE_ROOT=self._tmpdir)
        self._override.enable()

        self.province = Province.objects.create(name='闭环省', code='960000')
        self.city = City.objects.create(province=self.province, name='闭环市', code='960100')
        self.school = School.objects.create(city=self.city, name='闭环学校')
        self.school_user = self._user('13950000001', '校级管理员', UserRole.SCHOOL)
        SchoolProfile.objects.create(user=self.school_user, name='校级管理员', school=self.school)
        self.competition = Competition.objects.create(name='闭环赛事', year=2026)

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @staticmethod
    def _user(phone, name, role):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(
            username=phone, phone=phone, name=name, role=role, password='pass1234',
        )

    @staticmethod
    def _client_for(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
        return client

    def test_new_user_full_loop(self):
        # 1. 教练自助注册（profile 待审核、无身份证）
        coach_client = APIClient()
        resp = coach_client.post('/api/auth/register', {
            'name': '闭环教练', 'phone': '13950000002', 'password': 'pass1234', 'role': 'coach',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        coach = CoachProfile.objects.get(user__phone='13950000002')
        coach_client = self._client_for(coach.user)

        # 2. 教练补身份证，校级审核通过
        filled = coach_client.put('/api/coach/profile', {'id_card': '510100200008088901'}, format='json')
        self.assertEqual(filled.json()['code'], 0)
        school_client = self._client_for(self.school_user)
        listed = school_client.get('/api/school/coaches?status=pending')
        self.assertIn(coach.pk, [row['id'] for row in listed.json()['data']['list']])
        approved = school_client.post(
            f'/api/school/coaches/{coach.pk}/verify', {'approve': True}, format='json'
        )
        self.assertEqual(approved.json()['code'], 0)

        # 3. 教练建队，校级审核通过
        created_team = coach_client.post('/api/coach/teams', {
            'school_id': self.school.pk, 'name': '闭环队', 'group_type': 'middle_school',
        }, format='json')
        self.assertEqual(created_team.json()['code'], 0)
        team_id = created_team.json()['data']['id']
        verified_team = school_client.post(
            f'/api/school/teams/{team_id}/verify', {'approve': True}, format='json'
        )
        self.assertEqual(verified_team.json()['code'], 0)

        # 4. 球员自助注册、补身份证、校级审核通过
        athlete_client = APIClient()
        resp = athlete_client.post('/api/auth/register', {
            'name': '闭环球员', 'phone': '13950000003', 'password': 'pass1234', 'role': 'athlete',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        athlete = AthleteProfile.objects.get(user__phone='13950000003')
        athlete_client = self._client_for(athlete.user)
        self.assertEqual(
            athlete_client.put('/api/athlete/profile', {'id_card': '510100200009099012'}, format='json').json()['code'],
            0,
        )
        school_client.post(f'/api/school/athletes/{athlete.pk}/verify', {'approve': True}, format='json')

        # 5. 教练把球员加入本队
        added = coach_client.post(
            f'/api/coach/teams/{team_id}/athletes', {'athlete_id': athlete.pk}, format='json'
        )
        self.assertEqual(added.json()['code'], 0)

        # 6. 创建报名草稿并上传三类材料
        draft = coach_client.post('/api/registrations', {
            'competition_id': self.competition.pk, 'team_id': team_id,
        }, format='json')
        self.assertEqual(draft.json()['code'], 0)
        registration_id = draft.json()['data']['id']

        pdf = lambda name: SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')
        self.assertEqual(
            coach_client.post(
                f'/api/registrations/{registration_id}/stamped-form',
                {'file': pdf('盖章.pdf')}, format='multipart',
            ).json()['code'],
            0,
        )
        self.assertEqual(
            coach_client.post(
                f'/api/registrations/{registration_id}/coach-qualification',
                {'file': pdf('教练资质.pdf')}, format='multipart',
            ).json()['code'],
            0,
        )
        self.assertEqual(
            coach_client.post(
                f'/api/registrations/{registration_id}/athletes/{athlete.pk}/enrollment-cert',
                {'file': pdf('学籍.pdf')}, format='multipart',
            ).json()['code'],
            0,
        )

        # 7. 提交报名
        submitted = coach_client.post(f'/api/registrations/{registration_id}/submit')
        self.assertEqual(submitted.json()['code'], 0)
        registration = Registration.objects.get(pk=registration_id)
        self.assertEqual(registration.status, Registration.Status.SUBMITTED)
        self.assertEqual(len(registration.players_snapshot), 1)
        self.assertEqual(registration.players_snapshot[0]['name'], '闭环球员')
        self.assertEqual(registration.coach_snapshot['name'], '闭环教练')
