"""组织/院校/教练端回归测试。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, CityProfile, CoachProfile, SchoolProfile, UserRole
from apps.org.models import City, GroupType, Province, School, Team, TeamStatus

User = get_user_model()


def access_token(user):
    return str(RefreshToken.for_user(user).access_token)


class OrgPermissionTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='930000')
        self.city1 = City.objects.create(province=self.province, name='甲市', code='930100')
        self.city2 = City.objects.create(province=self.province, name='乙市', code='930200')
        self.school = School.objects.create(city=self.city1, name='甲校')
        self.school2 = School.objects.create(city=self.city2, name='乙校')

        self.admin = User.objects.create_user(
            username='org-admin@test.com', email='org-admin@test.com', name='管理员',
            role=UserRole.ADMIN, password='pass1234',
        )
        self.city_user = User.objects.create_user(
            username='13920000001', phone='13920000001', name='甲市管理员',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=self.city_user, name='甲市管理员', city=self.city1)
        self.school_user = User.objects.create_user(
            username='13920000002', phone='13920000002', name='甲校管理员',
            role=UserRole.SCHOOL, password='pass1234',
        )
        SchoolProfile.objects.create(user=self.school_user, name='甲校管理员', school=self.school)
        self.coach = User.objects.create_user(
            username='13920000003', phone='13920000003', name='教练',
            role=UserRole.COACH, password='pass1234',
        )
        CoachProfile.objects.create(user=self.coach, name='教练', status='approved')
        self.pending_coach = User.objects.create_user(
            username='13920000004', phone='13920000004', name='待审教练',
            role=UserRole.COACH, password='pass1234',
        )
        CoachProfile.objects.create(
            user=self.pending_coach, name='待审教练', status='pending',
            id_card='510100200001019999',
        )

    def auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(user)}')

    # ---------- 学校权限 ----------
    def test_school_cannot_create_school(self):
        self.auth(self.school_user)
        resp = self.client.post('/api/admin/schools', {'city': self.city1.pk, 'name': '新校'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 20004)

    def test_city_can_create_school_in_own_city(self):
        self.auth(self.city_user)
        resp = self.client.post('/api/admin/schools', {'city': self.city1.pk, 'name': '甲市新校'}, format='json')
        self.assertEqual(resp.json()['code'], 0)

    def test_city_cannot_create_school_in_other_city(self):
        self.auth(self.city_user)
        resp = self.client.post('/api/admin/schools', {'city': self.city2.pk, 'name': '跨市新校'}, format='json')
        self.assertEqual(resp.json()['code'], 20004)

    def test_city_can_view_own_school_detail_only(self):
        self.auth(self.city_user)
        self.assertEqual(self.client.get(f'/api/admin/schools/{self.school.pk}').json()['code'], 0)
        other = self.client.get(f'/api/admin/schools/{self.school2.pk}')
        self.assertEqual(other.json()['code'], 20004)

    def test_school_can_update_basic_info_only(self):
        self.auth(self.school_user)
        ok = self.client.put(
            f'/api/admin/schools/{self.school.pk}', {'name': '甲校改名', 'address': '新地址'}, format='json'
        )
        self.assertEqual(ok.json()['code'], 0)
        bad = self.client.put(
            f'/api/admin/schools/{self.school.pk}', {'status': 'disabled'}, format='json'
        )
        self.assertEqual(bad.json()['code'], 20004)

    def test_school_cannot_delete_school(self):
        self.auth(self.school_user)
        resp = self.client.delete(f'/api/admin/schools/{self.school.pk}')
        self.assertEqual(resp.json()['code'], 20004)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())

    def test_city_cannot_create_city(self):
        self.auth(self.city_user)
        resp = self.client.post(
            '/api/admin/cities', {'province': self.province.pk, 'name': '丙市', 'code': '930300'},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 20004)

    def test_delete_school_blocked_by_team(self):
        Team.objects.create(
            school=self.school, coach=self.coach, name='甲校队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        self.auth(self.admin)
        resp = self.client.delete(f'/api/admin/schools/{self.school.pk}')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())

    def test_delete_province_blocked_by_city(self):
        self.auth(self.admin)
        resp = self.client.delete(f'/api/admin/provinces/{self.province.pk}')
        self.assertEqual(resp.json()['code'], 10001)

    # ---------- 教练建队 / 校级审核 ----------
    def test_coach_can_fill_id_card_before_verify(self):
        """新注册教练需先自行填写身份证号，校级才能通过实名审核。"""
        self.auth(self.pending_coach)
        resp = self.client.put(
            '/api/coach/profile', {'id_card': '510100200006066789'}, format='json'
        )
        self.assertEqual(resp.json()['code'], 0)
        self.assertTrue(resp.json()['data']['id_card'].endswith('6789'))

    def test_unapproved_coach_cannot_create_team(self):
        self.auth(self.pending_coach)
        resp = self.client.post('/api/coach/teams', {
            'school_id': self.school.pk, 'name': '待审教练队', 'group_type': 'middle_school',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 20004)

    def test_unassigned_coach_can_be_verified_then_create_team(self):
        """新注册教练还没有球队，校级也应能看到并完成实名审核，之后才能建队。"""
        self.auth(self.school_user)
        listed = self.client.get('/api/school/coaches?status=pending')
        names = [row['name'] for row in listed.json()['data']['list']]
        self.assertIn('待审教练', names)

        profile = CoachProfile.objects.get(user=self.pending_coach)
        verified = self.client.post(
            f'/api/school/coaches/{profile.pk}/verify', {'approve': True}, format='json'
        )
        self.assertEqual(verified.json()['code'], 0)
        profile.refresh_from_db()
        self.assertEqual(profile.status, 'approved')

        coach_client = APIClient()
        coach_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(self.pending_coach)}')
        created = coach_client.post('/api/coach/teams', {
            'school_id': self.school.pk, 'name': '新教练队', 'group_type': 'middle_school',
        }, format='json')
        self.assertEqual(created.json()['code'], 0)
        self.assertEqual(created.json()['data']['status'], 'pending')

    def test_coach_create_team_enters_pending(self):
        self.auth(self.coach)
        resp = self.client.post('/api/coach/teams', {
            'school_id': self.school.pk, 'name': '教练队', 'group_type': 'middle_school',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['status'], 'pending')
        self.assertEqual(resp.json()['data']['school'], self.school.pk)

    def test_coach_cannot_edit_others_team(self):
        team = Team.objects.create(
            school=self.school, coach=self.pending_coach, name='别人的队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.PENDING,
        )
        self.auth(self.coach)
        resp = self.client.put(f'/api/coach/teams/{team.pk}', {'name': '改名'}, format='json')
        self.assertEqual(resp.json()['code'], 20004)

    def test_school_verifies_own_team(self):
        team = Team.objects.create(
            school=self.school, coach=self.coach, name='待审队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.PENDING,
        )
        self.auth(self.school_user)
        resp = self.client.post(f'/api/school/teams/{team.pk}/verify', {'approve': True}, format='json')
        self.assertEqual(resp.json()['code'], 0)
        team.refresh_from_db()
        self.assertEqual(team.status, TeamStatus.APPROVED)

    def test_school_cannot_verify_other_school_team(self):
        team = Team.objects.create(
            school=self.school2, coach=self.coach, name='乙校队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.PENDING,
        )
        self.auth(self.school_user)
        resp = self.client.post(f'/api/school/teams/{team.pk}/verify', {'approve': True}, format='json')
        self.assertEqual(resp.json()['code'], 20004)

    def test_school_team_list_scoped(self):
        Team.objects.create(
            school=self.school, coach=self.coach, name='甲校队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.PENDING,
        )
        Team.objects.create(
            school=self.school2, coach=self.coach, name='乙校队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.PENDING,
        )
        self.auth(self.school_user)
        resp = self.client.get('/api/school/teams')
        names = [row['name'] for row in resp.json()['data']['list']]
        self.assertEqual(names, ['甲校队'])

    def test_school_lists_coaches_and_athletes(self):
        Team.objects.create(
            school=self.school, coach=self.coach, name='甲校队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        other_athlete = User.objects.create_user(
            username='13920000005', phone='13920000005', name='球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        team = Team.objects.get(name='甲校队')
        AthleteProfile.objects.create(user=other_athlete, name='球员', team=team, status='pending')

        self.auth(self.school_user)
        coaches = self.client.get('/api/school/coaches')
        athletes = self.client.get('/api/school/athletes')
        self.assertEqual(coaches.json()['data']['total'], 2)
        self.assertEqual(athletes.json()['data']['total'], 1)

    def test_unassigned_athlete_can_be_verified(self):
        athlete_user = User.objects.create_user(
            username='13920000006', phone='13920000006', name='未挂队球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        profile = AthleteProfile.objects.create(
            user=athlete_user, name='未挂队球员', status='pending',
            id_card='510100200004044567',
        )
        self.auth(self.school_user)
        listed = self.client.get('/api/school/athletes?status=pending')
        self.assertIn('未挂队球员', [row['name'] for row in listed.json()['data']['list']])

        verified = self.client.post(
            f'/api/school/athletes/{profile.pk}/verify', {'approve': True}, format='json'
        )
        self.assertEqual(verified.json()['code'], 0)
        profile.refresh_from_db()
        self.assertEqual(profile.status, 'approved')

    def test_coach_adds_and_removes_team_athlete(self):
        team = Team.objects.create(
            school=self.school, coach=self.coach, name='名单队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.APPROVED,
        )
        athlete_user = User.objects.create_user(
            username='13920000007', phone='13920000007', name='队员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        profile = AthleteProfile.objects.create(
            user=athlete_user, name='队员', jersey_no=9, status='approved',
            id_card='510100200005055678',
        )
        self.auth(self.coach)
        added = self.client.post(
            f'/api/coach/teams/{team.pk}/athletes', {'athlete_id': profile.pk}, format='json'
        )
        self.assertEqual(added.json()['code'], 0)
        profile.refresh_from_db()
        self.assertEqual(profile.team_id, team.pk)

        listed = self.client.get(f'/api/coach/teams/{team.pk}/athletes')
        self.assertEqual(len(listed.json()['data']), 1)

        removed = self.client.delete(f'/api/coach/teams/{team.pk}/athletes/{profile.pk}')
        self.assertEqual(removed.json()['code'], 0)
        profile.refresh_from_db()
        self.assertIsNone(profile.team_id)


