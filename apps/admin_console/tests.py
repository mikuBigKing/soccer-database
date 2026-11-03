"""管理端球员 / 教练员 14 个接口测试（正常流 + 异常流）。"""
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, CoachProfile, ProfileStatus, UserStatus
from apps.admin_console.models import (
    AdminOperationLog, PlayerSuspension, PlayerTeamChange,
)
from apps.live.models import Match, MatchEvent
from apps.org.models import City, GroupType, Province, School, Team, TeamStatus
from apps.registration.models import Competition, Registration

User = get_user_model()

ID_CARD_1 = '11010519491231002X'
ID_CARD_2 = '110101199003077758'
ID_CARD_3 = '110101199003077766'
# 阶段一新增球员用（校验位均正确）
NEW_ID_CARD_1 = '110101198001010037'
NEW_ID_CARD_2 = '110101198001010045'
NEW_ID_CARD_3 = '110101198001010053'
PASSWORD = 'Passw0rd!'


def make_user(username, role, phone=None, email=None, password=PASSWORD, name=''):
    return User.objects.create_user(
        username=username, role=role, phone=phone, email=email, password=password, name=name,
    )


def auth_client(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return client


class BaseAdminData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin', 'admin', email='admin@example.com', name='管理员')
        cls.outsider = make_user('outsider', 'personal', phone='13900000001', name='路人')

        province = Province.objects.create(name='测试省', code='110000')
        city = City.objects.create(province=province, name='测试市', code='110100')
        cls.school = School.objects.create(city=city, name='测试学校')

        cls.coach_user = make_user('coach1', 'coach', phone='13700000001', name='李教练')
        cls.coach2_user = make_user('coach2', 'coach', phone='13700000002', name='王教练')
        cls.coach3_user = make_user('coach3', 'coach', phone='13700000003', name='赵教练')

        cls.team = Team.objects.create(
            school=cls.school, coach=cls.coach_user, name='测试一队',
            group_type=GroupType.MIDDLE_SCHOOL,
        )

        cls.p1_user = make_user('athlete1', 'athlete', phone='13800000001', name='张三')
        cls.p2_user = make_user('athlete2', 'athlete', phone='13800000002', name='王五')
        cls.p3_user = make_user('athlete3', 'athlete', phone='13800000003', name='赵六')

        cls.p1 = AthleteProfile.objects.create(
            user=cls.p1_user, team=cls.team, name='张三', jersey_no=10, position='前锋',
            id_card=ID_CARD_1, avatar_path='/media/avatar/1.jpg',
            status=ProfileStatus.APPROVED,
        )
        cls.p2 = AthleteProfile.objects.create(
            user=cls.p2_user, name='王五', jersey_no=7, position='中场',
            id_card=ID_CARD_2, status=ProfileStatus.PENDING,
        )
        cls.p3 = AthleteProfile.objects.create(
            user=cls.p3_user, name='赵六', jersey_no=9,
            status=ProfileStatus.APPROVED, is_public=True,
        )

        cls.c1 = CoachProfile.objects.create(
            user=cls.coach_user, name='李教练', phone='13700000001', id_card=ID_CARD_1,
            qualification_path='/media/qual/1.pdf', status=ProfileStatus.APPROVED,
        )
        cls.c2 = CoachProfile.objects.create(
            user=cls.coach2_user, name='王教练', phone='13700000002', id_card=ID_CARD_2,
            status=ProfileStatus.PENDING,
        )
        cls.c3 = CoachProfile.objects.create(
            user=cls.coach3_user, name='赵教练', phone='13700000003',
            status=ProfileStatus.APPROVED, is_public=True,
        )

        cls.admin_client = auth_client(cls.admin)
        cls.outsider_client = auth_client(cls.outsider)


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

class PermissionTest(BaseAdminData):
    def test_anonymous_rejected(self):
        resp = APIClient().get('/api/admin/players')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['code'], 20001)

    def test_non_admin_rejected(self):
        for path in ('/api/admin/players', '/api/admin/coaches'):
            resp = self.outsider_client.get(path)
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(resp.json()['code'], 20004)

    def test_trailing_slash_not_registered(self):
        resp = self.admin_client.get('/api/admin/players/')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# 球员
# ---------------------------------------------------------------------------

class PlayerListTest(BaseAdminData):
    def test_shape_and_pagination(self):
        resp = self.admin_client.get('/api/admin/players', {'page': 1, 'page_size': 1})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 0)
        self.assertTrue(body['success'])
        self.assertEqual(set(body['data']), {'list', 'total', 'page', 'page_size', 'next_page'})
        self.assertEqual(body['data']['total'], 3)
        self.assertEqual(len(body['data']['list']), 1)
        self.assertEqual(body['data']['page'], 1)
        self.assertEqual(body['data']['page_size'], 1)
        self.assertEqual(body['data']['next_page'], 2)

    def test_item_fields(self):
        rows = self.admin_client.get('/api/admin/players').json()['data']['list']
        item = [row for row in rows if row['id'] == self.p1.id][0]
        self.assertEqual(set(item), {
            'id', 'name', 'gender', 'jersey_no', 'position', 'team_id', 'team_name',
            'group_type', 'school_id', 'school_name', 'grade', 'height_cm', 'weight_kg',
            'status', 'is_public', 'real_name_status', 'enrollment_status',
            'eligibility_status', 'avatar', 'created_at', 'deleted_at',
        })
        self.assertEqual(item['school_name'], '测试学校')
        self.assertEqual(item['real_name_status'], 'unverified')
        self.assertEqual(item['eligibility_status'], 'eligible')

    def test_filters(self):
        cases = [
            ({'status': 'approved'}, 2),
            ({'is_public': 'true'}, 1),
            ({'group_type': 'middle_school'}, 1),
            ({'jersey_no': 7}, 1),
            ({'team_id': self.team.id}, 1),
            ({'name': '张'}, 1),
            ({'id_card': ID_CARD_1}, 1),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                data = self.admin_client.get('/api/admin/players', params).json()['data']
                self.assertEqual(data['total'], expected)

    def test_keyword_hits_name_phone_and_id_card(self):
        for keyword in ('张三', '13800000002', ID_CARD_1):
            with self.subTest(keyword=keyword):
                data = self.admin_client.get('/api/admin/players', {'keyword': keyword}).json()['data']
                self.assertGreaterEqual(data['total'], 1)

    def test_page_size_clamped_to_100(self):
        data = self.admin_client.get('/api/admin/players', {'page_size': 500}).json()['data']
        self.assertEqual(data['page_size'], 100)

    def test_page_beyond_range_reports_real_page(self):
        data = self.admin_client.get('/api/admin/players', {'page': 99, 'page_size': 2}).json()['data']
        self.assertEqual(data['page'], 2)
        self.assertEqual(len(data['list']), 1)
        self.assertIsNone(data['next_page'])

    def test_first_page_report_unchanged(self):
        data = self.admin_client.get('/api/admin/players', {'page': 1, 'page_size': 2}).json()['data']
        self.assertEqual(data['page'], 1)
        self.assertEqual(len(data['list']), 2)
        self.assertEqual(data['next_page'], 2)

    def test_id_card_search_case_insensitive(self):
        for value in (ID_CARD_1, ID_CARD_1.lower()):
            with self.subTest(value=value):
                data = self.admin_client.get('/api/admin/players', {'id_card': value}).json()['data']
                self.assertEqual(data['total'], 1)

    def test_id_card_search_uppercase_against_lowercase_stored(self):
        # 模拟历史数据以大写 X 以外的小写 x 落库
        AthleteProfile.objects.filter(pk=self.p1.pk).update(id_card=ID_CARD_1.lower())
        data = self.admin_client.get('/api/admin/players', {'id_card': ID_CARD_1}).json()['data']
        self.assertEqual(data['total'], 1)

    def test_empty_result_beyond_range_falls_back_to_page_1(self):
        data = self.admin_client.get(
            '/api/admin/players', {'status': 'rejected', 'page': 99, 'page_size': 2},
        ).json()['data']
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['page'], 1)
        self.assertEqual(data['list'], [])
        self.assertIsNone(data['next_page'])

    def test_invalid_filter_value(self):
        resp = self.admin_client.get('/api/admin/players', {'status': 'nope'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('status', resp.json()['msg'])

    def test_invalid_page_size(self):
        resp = self.admin_client.get('/api/admin/players', {'page_size': 'abc'})
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('page_size', resp.json()['msg'])


class PlayerDetailTest(BaseAdminData):
    def test_detail_masks_sensitive_fields(self):
        resp = self.admin_client.get(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(set(data), {
            'id', 'name', 'gender', 'jersey_no', 'position', 'team_id', 'team_name', 'group_type',
            'school_id', 'school_name', 'grade', 'height_cm', 'weight_kg',
            'status', 'is_public', 'real_name_status', 'enrollment_status',
            'eligibility_status', 'avatar', 'created_at', 'deleted_at',
            'id_card', 'phone', 'enrollment_cert', 'reject_reason', 'suspensions', 'updated_at',
        })
        self.assertEqual(data['id_card'], '110***XXXX****002X')
        self.assertEqual(data['phone'], '138****0001')
        self.assertEqual(data['team_name'], '测试一队')
        self.assertEqual(data['group_type'], 'middle_school')

    def test_not_found(self):
        resp = self.admin_client.get('/api/admin/players/999999')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['code'], 30001)
        self.assertEqual(resp.json()['msg'], '球员不存在')


class PlayerUpdateTest(BaseAdminData):
    def test_partial_update(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'position': '后腰'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.position, '后腰')
        self.assertEqual(self.p2.name, '王五')
        self.assertEqual(resp.json()['data']['position'], '后腰')

    def test_forbidden_fields_ignored(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}',
            {'status': 'approved', 'team_id': self.team.id, 'is_public': True, 'user': 1},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.status, ProfileStatus.PENDING)
        self.assertIsNone(self.p2.team_id)
        self.assertFalse(self.p2.is_public)

    def test_invalid_id_card_and_phone(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'id_card': '110105194912310021'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('id_card', resp.json()['msg'])

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'phone': '12345'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('phone', resp.json()['msg'])

    def test_empty_body_returns_detail_without_log(self):
        log_count = AdminOperationLog.objects.count()
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}', {}, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['id'], self.p2.id)
        self.assertEqual(AdminOperationLog.objects.count(), log_count)

    def test_phone_written_to_linked_user(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'phone': '13600000009'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p2_user.refresh_from_db()
        self.assertEqual(self.p2_user.phone, '13600000009')
        self.assertEqual(resp.json()['data']['phone'], '136****0009')

    def test_duplicate_phone_rejected(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'phone': '13800000001'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('该手机号已被使用', resp.json()['msg'])

    def test_not_found(self):
        resp = self.admin_client.put('/api/admin/players/999999', {'name': 'x'}, format='json')
        self.assertEqual(resp.json()['code'], 30001)


class PlayerDeleteTest(BaseAdminData):
    def test_soft_delete(self):
        resp = self.admin_client.delete(f'/api/admin/players/{self.p2.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.p2.id, 'deleted': True})
        self.p2.refresh_from_db()
        self.assertIsNotNone(self.p2.deleted_at)

        ids = [item['id'] for item in self.admin_client.get('/api/admin/players').json()['data']['list']]
        self.assertNotIn(self.p2.id, ids)
        self.assertEqual(self.admin_client.get(f'/api/admin/players/{self.p2.id}').json()['code'], 30001)

    def test_blocked_when_in_team(self):
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已加入球队，不可删除')

    def test_public_flag_cleared_on_delete(self):
        self.assertTrue(self.p3.is_public)
        resp = self.admin_client.delete(f'/api/admin/players/{self.p3.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.p3.refresh_from_db()
        self.assertFalse(self.p3.is_public)
        self.assertIsNotNone(self.p3.deleted_at)

    def test_rejected_team_allows_delete(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.p1.refresh_from_db()
        self.assertIsNotNone(self.p1.deleted_at)

    def test_approved_team_still_blocks_delete(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.APPROVED)
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已加入球队，不可删除')


class PlayerPublishTest(BaseAdminData):
    def test_publish_requires_approved(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/publish')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '仅审核通过的球员可公示')

    def test_publish_success_and_duplicate(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/publish')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.p1.id, 'is_public': True})
        self.p1.refresh_from_db()
        self.assertTrue(self.p1.is_public)

        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/publish')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已公示')

    def test_unpublish(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.p3.id, 'is_public': False})

        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员未公示')

    def test_actions_on_missing_player(self):
        for suffix in ('publish', 'unpublish', 'reset-password'):
            with self.subTest(suffix=suffix):
                resp = self.admin_client.post(f'/api/admin/players/999999/{suffix}')
                self.assertEqual(resp.json()['code'], 30001)


class PlayerResetPasswordTest(BaseAdminData):
    def test_password_reset(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/reset-password')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.p1.id, 'reset': True})

        self.p1_user.refresh_from_db()
        self.assertTrue(check_password(settings.DEFAULT_RESET_PASSWORD, self.p1_user.password))
        self.assertFalse(check_password(PASSWORD, self.p1_user.password))

    def test_no_plaintext_in_response_or_log(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/reset-password')
        self.assertNotIn(settings.DEFAULT_RESET_PASSWORD, str(resp.json()))

        log = AdminOperationLog.objects.filter(action='重置球员密码').latest('id')
        self.assertIsNone(log.before)
        self.assertIsNone(log.after)
        self.assertNotIn(settings.DEFAULT_RESET_PASSWORD, f'{log.before}{log.after}')


class PlayerOperationLogTest(BaseAdminData):
    def test_update_and_publish_are_logged(self):
        self.admin_client.put(f'/api/admin/players/{self.p2.id}', {'position': '后卫'}, format='json')
        self.admin_client.post(f'/api/admin/players/{self.p2.id}/publish')

        self.assertTrue(AdminOperationLog.objects.filter(
            action='修改球员', target_type='player', target_id=self.p2.id,
        ).exists())
        log = AdminOperationLog.objects.filter(action='修改球员').latest('id')
        self.assertEqual(log.operator_id, self.admin.id)
        self.assertEqual(log.before['position'], '中场')
        self.assertEqual(log.after['position'], '后卫')
        self.assertEqual(log.before['id_card'], '110***XXXX****7758')


# ---------------------------------------------------------------------------
# 教练员
# ---------------------------------------------------------------------------

class CoachListTest(BaseAdminData):
    def test_shape_phone_masked(self):
        resp = self.admin_client.get('/api/admin/coaches')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(data['total'], 3)
        item = [row for row in data['list'] if row['id'] == self.c1.id][0]
        self.assertEqual(item['phone'], '137****0001')
        self.assertEqual(set(item), {
            'id', 'name', 'phone', 'qualification', 'status', 'is_public', 'created_at',
            'deleted_at',
        })

    def test_filters(self):
        cases = [
            ({'status': 'approved'}, 2),
            ({'is_public': 'true'}, 1),
            ({'group_type': 'middle_school'}, 1),
            ({'team_id': self.team.id}, 1),
            ({'name': '李'}, 1),
            ({'id_card': ID_CARD_2}, 1),
            ({'keyword': '13700000003'}, 1),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                data = self.admin_client.get('/api/admin/coaches', params).json()['data']
                self.assertEqual(data['total'], expected)

    def test_page_beyond_range_reports_real_page(self):
        data = self.admin_client.get('/api/admin/coaches', {'page': 99, 'page_size': 2}).json()['data']
        self.assertEqual(data['page'], 2)
        self.assertEqual(len(data['list']), 1)
        self.assertIsNone(data['next_page'])

    def test_id_card_search_case_insensitive(self):
        for value in (ID_CARD_1, ID_CARD_1.lower()):
            with self.subTest(value=value):
                data = self.admin_client.get('/api/admin/coaches', {'id_card': value}).json()['data']
                self.assertEqual(data['total'], 1)


class CoachDetailTest(BaseAdminData):
    def test_detail_with_teams(self):
        resp = self.admin_client.get(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(data['id_card'], '110***XXXX****002X')
        self.assertEqual(len(data['teams']), 1)
        self.assertEqual(data['teams'][0]['name'], '测试一队')
        self.assertEqual(data['teams'][0]['group_type'], 'middle_school')

    def test_not_found(self):
        resp = self.admin_client.get('/api/admin/coaches/999999')
        self.assertEqual(resp.json()['code'], 30001)
        self.assertEqual(resp.json()['msg'], '教练员不存在')


class CoachUpdateTest(BaseAdminData):
    def test_partial_update(self):
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}',
            {'name': '王教练改', 'qualification': '/media/qual/2.pdf'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.c2.refresh_from_db()
        self.assertEqual(self.c2.name, '王教练改')
        self.assertEqual(self.c2.qualification_path, '/media/qual/2.pdf')

    def test_forbidden_fields_ignored(self):
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'status': 'approved', 'is_public': True}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.c2.refresh_from_db()
        self.assertEqual(self.c2.status, ProfileStatus.PENDING)
        self.assertFalse(self.c2.is_public)

    def test_validation_errors(self):
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'phone': '1'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('phone', resp.json()['msg'])

        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'id_card': '110101199003077750'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('id_card', resp.json()['msg'])

    def test_empty_body_returns_detail(self):
        log_count = AdminOperationLog.objects.count()
        resp = self.admin_client.put(f'/api/admin/coaches/{self.c2.id}', {}, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['id'], self.c2.id)
        self.assertEqual(AdminOperationLog.objects.count(), log_count)


class CoachDeleteTest(BaseAdminData):
    def test_blocked_when_coaching(self):
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['msg'], '该教练员有在执球队，不可删除')

    def test_soft_delete_and_public_cleared(self):
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c3.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.c3.refresh_from_db()
        self.assertIsNotNone(self.c3.deleted_at)
        self.assertFalse(self.c3.is_public)
        self.assertEqual(self.admin_client.get(f'/api/admin/coaches/{self.c3.id}').json()['code'], 30001)

    def test_rejected_team_allows_delete(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.c1.refresh_from_db()
        self.assertIsNotNone(self.c1.deleted_at)

    def test_approved_team_still_blocks_delete(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.APPROVED)
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该教练员有在执球队，不可删除')

    def test_rejected_plus_pending_teams_still_blocks_delete(self):
        # 驳回球队放行，但只要还有一支待审核/已通过球队就必须拦
        Team.objects.create(
            school=self.school, coach=self.coach_user, name='测试二队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.REJECTED,
        )
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该教练员有在执球队，不可删除')


class CoachPublishTest(BaseAdminData):
    def test_publish_rules(self):
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c2.id}/publish')
        self.assertEqual(resp.json()['msg'], '仅审核通过的教练员可公示')

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.c1.id, 'is_public': True})

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish')
        self.assertEqual(resp.json()['msg'], '该教练员已公示')

    def test_unpublish_rules(self):
        resp = self.admin_client.post(
            f'/api/admin/coaches/{self.c3.id}/unpublish',
            {'reason': '公示材料存在异议'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.c3.id, 'is_public': False})
        resp = self.admin_client.post(
            f'/api/admin/coaches/{self.c3.id}/unpublish',
            {'reason': '公示材料存在异议'}, format='json',
        )
        self.assertEqual(resp.json()['msg'], '该教练员未公示')

    def test_missing_coach(self):
        resp = self.admin_client.post('/api/admin/coaches/999999/publish')
        self.assertEqual(resp.json()['msg'], '教练员不存在')


class CoachResetPasswordTest(BaseAdminData):
    def test_password_reset(self):
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/reset-password')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.c1.id, 'reset': True})

        self.coach_user.refresh_from_db()
        self.assertTrue(check_password(settings.DEFAULT_RESET_PASSWORD, self.coach_user.password))
        self.assertNotIn(settings.DEFAULT_RESET_PASSWORD, str(resp.json()))


class MaskingTest(TestCase):
    def test_masks(self):
        from apps.admin_console.masking import mask_id_card, mask_phone

        self.assertEqual(mask_id_card(ID_CARD_1), '110***XXXX****002X')
        self.assertEqual(mask_phone('13800005678'), '138****5678')
        self.assertEqual(mask_id_card(''), '')
        self.assertEqual(mask_phone(None or ''), '')


# ---------------------------------------------------------------------------
# 缺陷回归：手机号与登录账号同步（缺陷 1 / 2）
# ---------------------------------------------------------------------------

def login(client, phone, password=PASSWORD):
    return client.post('/api/auth/login', {'phone': phone, 'password': password}, format='json')


class UserPhoneSyncTest(BaseAdminData):
    def test_coach_phone_synced_and_login_switches(self):
        new_phone = '13600000009'
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'phone': new_phone}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)

        self.c2.refresh_from_db()
        self.coach2_user.refresh_from_db()
        self.assertEqual(self.c2.phone, new_phone)
        self.assertEqual(self.coach2_user.phone, new_phone)
        self.assertEqual(self.coach2_user.username, new_phone)

        client = APIClient()
        self.assertEqual(login(client, '13700000002').json()['code'], 20002)
        self.assertEqual(login(client, new_phone).json()['code'], 0)

    def test_coach_phone_conflict_rejected(self):
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'phone': '13700000003'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'phone: 该手机号已被使用')

    def test_player_phone_synced_and_old_phone_reusable(self):
        old_phone = '13800000002'
        new_phone = '13600000008'
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'phone': new_phone}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)

        self.p2_user.refresh_from_db()
        self.assertEqual(self.p2_user.phone, new_phone)
        self.assertEqual(self.p2_user.username, new_phone)

        # 旧号已让出，重新注册不应撞 username 唯一约束（不能 500）
        resp = APIClient().post('/api/auth/register', {
            'name': '新人', 'phone': old_phone, 'password': PASSWORD, 'role': 'personal',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['code'], 0)

    def test_player_phone_conflict_rejected(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'phone': '13800000001'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'phone: 该手机号已被使用')


# ---------------------------------------------------------------------------
# 缺陷回归：身份证重复校验（缺陷 3）
# ---------------------------------------------------------------------------

class IdCardUniqueTest(BaseAdminData):
    def test_player_id_card_conflict_rejected(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'id_card': ID_CARD_1}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'id_card: 该身份证号已被使用')
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.id_card, ID_CARD_2)

    def test_soft_deleted_player_id_card_not_blocking(self):
        AthleteProfile.objects.filter(pk=self.p1.pk).update(deleted_at=timezone.now())

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p3.id}', {'id_card': ID_CARD_1}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p3.refresh_from_db()
        self.assertEqual(self.p3.id_card, ID_CARD_1)

    def test_coach_id_card_conflict_and_tables_independent(self):
        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'id_card': ID_CARD_1}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'id_card: 该身份证号已被使用')

        # 球员表先占用 ID_CARD_3，不应阻塞教练表
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p3.id}', {'id_card': ID_CARD_3}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.put(
            f'/api/admin/coaches/{self.c2.id}', {'id_card': ID_CARD_3}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.c2.refresh_from_db()
        self.assertEqual(self.c2.id_card, ID_CARD_3)

    def test_same_id_card_on_self_not_conflict(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'id_card': ID_CARD_2}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)


# ---------------------------------------------------------------------------
# 缺陷回归：审计日志 IP 防伪造（缺陷 4）
# ---------------------------------------------------------------------------

class ClientIpTest(BaseAdminData):
    def test_xff_ignored_when_proxy_not_trusted(self):
        self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'position': '后卫'}, format='json',
            REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR='1.2.3.4, 5.6.7.8',
        )
        log = AdminOperationLog.objects.filter(action='修改球员').latest('id')
        self.assertEqual(log.ip, '203.0.113.9')

    @override_settings(TRUSTED_PROXY_IPS=['203.0.113.9'])
    def test_xff_used_when_proxy_trusted(self):
        self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'position': '后卫'}, format='json',
            REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR='1.2.3.4, 5.6.7.8',
        )
        log = AdminOperationLog.objects.filter(action='修改球员').latest('id')
        self.assertEqual(log.ip, '1.2.3.4')

    @override_settings(TRUSTED_PROXY_IPS=['203.0.113.9'])
    def test_xff_empty_first_value_falls_back(self):
        self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'position': '后卫'}, format='json',
            REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR=' , 1.2.3.4',
        )
        log = AdminOperationLog.objects.filter(action='修改球员').latest('id')
        self.assertEqual(log.ip, '203.0.113.9')


# ---------------------------------------------------------------------------
# 缺陷回归：公示行锁不改变原有行为（缺陷 5）
# ---------------------------------------------------------------------------

class PublishRowLockTest(BaseAdminData):
    def test_repeat_publish_still_rejected(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/publish')
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.post(f'/api/admin/players/{self.p1.id}/publish')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已公示')

        self.assertEqual(
            AdminOperationLog.objects.filter(action='公示球员', target_id=self.p1.id).count(), 1,
        )

    def test_repeat_unpublish_still_rejected(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员未公示')


# ---------------------------------------------------------------------------
# 缺陷回归：删除后账号不能登录（缺陷 6）
# ---------------------------------------------------------------------------

class DeletedAccountTest(BaseAdminData):
    def test_deleted_player_cannot_login(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/players/{self.p2.id}').json()['code'], 0,
        )
        self.p2_user.refresh_from_db()
        self.assertEqual(self.p2_user.status, UserStatus.DISABLED)
        self.assertFalse(self.p2_user.is_active)

        self.assertEqual(login(APIClient(), '13800000002').json()['code'], 20003)
        resp = self.admin_client.get(f'/api/admin/players/{self.p2.id}')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['code'], 30001)

    def test_deleted_coach_cannot_login(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/coaches/{self.c3.id}').json()['code'], 0,
        )
        self.coach3_user.refresh_from_db()
        self.assertEqual(self.coach3_user.status, UserStatus.DISABLED)
        self.assertFalse(self.coach3_user.is_active)

        self.assertEqual(login(APIClient(), '13700000003').json()['code'], 20003)
        self.assertEqual(
            self.admin_client.get(f'/api/admin/coaches/{self.c3.id}').json()['code'], 30001,
        )

    def test_deleted_player_token_invalidated(self):
        player_client = auth_client(self.p2_user)
        self.assertEqual(player_client.get('/api/auth/me').status_code, 200)

        self.admin_client.delete(f'/api/admin/players/{self.p2.id}')

        # is_active=False 之后，删除前签发的 JWT 立即失效
        self.assertEqual(player_client.get('/api/auth/me').status_code, 401)

    def test_delete_keeps_operation_log(self):
        before = AdminOperationLog.objects.filter(action='删除球员').count()
        self.admin_client.delete(f'/api/admin/players/{self.p2.id}')

        self.assertEqual(AdminOperationLog.objects.filter(action='删除球员').count(), before + 1)
        log = AdminOperationLog.objects.filter(action='删除球员').latest('id')
        self.assertEqual(log.target_type, 'player')
        self.assertEqual(log.target_id, self.p2.id)
        self.assertEqual(log.operator_id, self.admin.id)


# ===========================================================================
# 阶段一扩展测试：新增球员 / 档案字段 / 状态标记 / 批量与归档
# ===========================================================================

class PlayerCreateTest(BaseAdminData):
    def test_create_creates_default_account_and_profile(self):
        resp = self.admin_client.post('/api/admin/players', {
            'name': '新球员', 'phone': '13600000011', 'id_card': NEW_ID_CARD_1,
            'school_id': self.school.id, 'grade': '初二', 'jersey_no': 11,
            'height_cm': 175, 'weight_kg': 60,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(data['name'], '新球员')
        self.assertEqual(data['status'], ProfileStatus.PENDING)
        self.assertEqual(data['school_id'], self.school.id)
        self.assertEqual(data['school_name'], '测试学校')
        self.assertEqual(data['height_cm'], 175)
        self.assertEqual(data['weight_kg'], 60)
        self.assertEqual(data['real_name_status'], 'unverified')
        self.assertEqual(data['eligibility_status'], 'eligible')

        user = User.objects.get(phone='13600000011')
        self.assertEqual(user.role, 'athlete')
        self.assertEqual(user.username, '13600000011')
        self.assertTrue(check_password(settings.DEFAULT_RESET_PASSWORD, user.password))
        # 默认密码可登录
        self.assertEqual(
            login(APIClient(), '13600000011', settings.DEFAULT_RESET_PASSWORD).json()['code'], 0,
        )

        self.assertTrue(
            AdminOperationLog.objects.filter(action='新增球员', target_id=data['id']).exists(),
        )

    def test_create_requires_name_phone_and_id_card(self):
        cases = [
            ({'phone': '13600000012', 'id_card': NEW_ID_CARD_2}, 'name'),
            ({'name': '缺手机号', 'id_card': NEW_ID_CARD_2}, 'phone'),
            ({'name': '缺身份证', 'phone': '13600000012'}, 'id_card'),
        ]
        for payload, field in cases:
            with self.subTest(field=field):
                resp = self.admin_client.post('/api/admin/players', payload, format='json')
                self.assertEqual(resp.json()['code'], 10001)
                self.assertIn(field, resp.json()['msg'])

    def test_create_duplicate_phone_and_id_card(self):
        resp = self.admin_client.post('/api/admin/players', {
            'name': '撞手机号', 'phone': '13800000001', 'id_card': NEW_ID_CARD_2,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'phone: 该手机号已被使用')

        resp = self.admin_client.post('/api/admin/players', {
            'name': '撞身份证', 'phone': '13600000012', 'id_card': ID_CARD_1,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'id_card: 该身份证号已被使用')

    def test_create_school_and_id_card_validation(self):
        resp = self.admin_client.post('/api/admin/players', {
            'name': '学校不存在', 'phone': '13600000013', 'id_card': NEW_ID_CARD_2,
            'school_id': 999999,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'school_id: 学校不存在')

        resp = self.admin_client.post('/api/admin/players', {
            'name': '校验位错误', 'phone': '13600000014', 'id_card': '110101198001010030',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('id_card', resp.json()['msg'])


class PlayerGenderTest(BaseAdminData):
    def test_create_with_gender_persisted(self):
        resp = self.admin_client.post('/api/admin/players', {
            'name': '女球员', 'phone': '13600000015', 'id_card': NEW_ID_CARD_3,
            'gender': 'female',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['gender'], 'female')
        new_player = AthleteProfile.objects.get(pk=resp.json()['data']['id'])
        self.assertEqual(new_player.gender, 'female')

    def test_update_gender_and_invalid_choice(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'gender': 'male'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.gender, 'male')

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'gender': 'other'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('gender', resp.json()['msg'])
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.gender, 'male')

        resp = self.admin_client.post('/api/admin/players', {
            'name': '非法性别', 'phone': '13600000016', 'id_card': NEW_ID_CARD_3,
            'gender': 'other',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('gender', resp.json()['msg'])


class PlayerProfileFieldTest(BaseAdminData):
    def test_update_profile_fields(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}', {
            'school_id': self.school.id, 'grade': '高一', 'height_cm': 180, 'weight_kg': 70,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.school_id, self.school.id)
        self.assertEqual(self.p2.grade, '高一')
        self.assertEqual(self.p2.height_cm, 180)
        self.assertEqual(self.p2.weight_kg, 70)
        self.assertEqual(resp.json()['data']['school_name'], '测试学校')

    def test_range_validation_and_clear_school(self):
        for field in ('height_cm', 'weight_kg'):
            with self.subTest(field=field):
                resp = self.admin_client.put(
                    f'/api/admin/players/{self.p2.id}', {field: 300}, format='json',
                )
                self.assertEqual(resp.json()['code'], 10001)
                self.assertIn('取值范围 0–220', resp.json()['msg'])

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}', {'school_id': None}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertIsNone(self.p2.school_id)

    def test_archived_list(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/players/{self.p2.id}').json()['code'], 0,
        )
        archived = self.admin_client.get('/api/admin/players', {'deleted': 'true'}).json()['data']
        self.assertEqual([item['id'] for item in archived['list']], [self.p2.id])
        self.assertIsNotNone(archived['list'][0]['deleted_at'])

        normal = self.admin_client.get('/api/admin/players').json()['data']
        self.assertNotIn(self.p2.id, [item['id'] for item in normal['list']])


class PlayerStatusFlagsTest(BaseAdminData):
    def test_update_flags_and_write_log(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/status-flags', {
            'real_name_status': 'verified', 'enrollment_status': 'verified',
            'eligibility_status': 'suspended',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['real_name_status'], 'verified')
        self.assertEqual(resp.json()['data']['eligibility_status'], 'suspended')
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.eligibility_status, 'suspended')
        self.assertTrue(
            AdminOperationLog.objects.filter(action='修改球员状态', target_id=self.p1.id).exists(),
        )

    def test_disqualified_requires_reason(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/status-flags', {
            'eligibility_status': 'disqualified',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('reason', resp.json()['msg'])

        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/status-flags', {
            'eligibility_status': 'disqualified', 'reason': '原因',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/status-flags', {
            'eligibility_status': 'disqualified', 'reason': '因严重违纪取消参赛资格',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['eligibility_status'], 'disqualified')

    def test_empty_and_invalid_payload(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p1.id}/status-flags', {}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('至少传一个状态字段', resp.json()['msg'])

        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/status-flags', {
            'real_name_status': 'bad',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)


class PlayerBatchAndRestoreTest(BaseAdminData):
    def test_batch_reset_skips_missing_and_deleted(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/players/{self.p2.id}').json()['code'], 0,
        )
        resp = self.admin_client.post('/api/admin/players/batch-reset-password', {
            'ids': [self.p1.id, self.p2.id, 999999],
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['success'], 1)
        self.assertEqual({item['id'] for item in data['skipped']}, {self.p2.id, 999999})
        self.assertEqual(data['password'], settings.DEFAULT_RESET_PASSWORD)

        self.p1_user.refresh_from_db()
        self.assertTrue(check_password(settings.DEFAULT_RESET_PASSWORD, self.p1_user.password))

    def test_batch_reset_custom_password_not_echoed(self):
        resp = self.admin_client.post('/api/admin/players/batch-reset-password', {
            'ids': [self.p1.id], 'password': 'aB345678',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertNotIn('password', resp.json()['data'])
        self.assertEqual(login(APIClient(), '13800000001', 'aB345678').json()['code'], 0)

    def test_batch_reset_rejects_weak_password_and_empty_ids(self):
        resp = self.admin_client.post('/api/admin/players/batch-reset-password', {
            'ids': [self.p1.id], 'password': '12345678',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('password', resp.json()['msg'])

        resp = self.admin_client.post(
            '/api/admin/players/batch-reset-password', {'ids': []}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)

    def test_restore_reactivates_account(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/players/{self.p2.id}').json()['code'], 0,
        )
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/restore')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.p2_user.refresh_from_db()
        self.assertIsNone(self.p2.deleted_at)
        self.assertEqual(self.p2_user.status, UserStatus.ACTIVE)
        self.assertTrue(self.p2_user.is_active)
        self.assertEqual(login(APIClient(), '13800000002').json()['code'], 0)

        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/restore')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员未在归档中')

    def test_restore_blocked_when_id_card_taken(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/players/{self.p2.id}').json()['code'], 0,
        )
        AthleteProfile.objects.filter(pk=self.p3.pk).update(id_card=ID_CARD_2)

        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/restore')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '原身份证号已被其他在册球员占用，无法恢复')


class PlayerDeleteRelationTest(BaseAdminData):
    def _make_registration(self, status):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        competition = Competition.objects.create(name='测试赛事', year=2026)
        return Registration.objects.create(
            competition=competition, team=self.team, status=status,
        )

    def test_blocked_by_in_progress_registration(self):
        self._make_registration(Registration.Status.SUBMITTED)
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '球员所在球队有进行中的报名，不可删除')

    def test_draft_registration_allows_delete(self):
        self._make_registration(Registration.Status.DRAFT)
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 0)
        self.p1.refresh_from_db()
        self.assertIsNotNone(self.p1.deleted_at)

    def test_blocked_by_match_event(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        match = Match.objects.create(
            group_type=GroupType.MIDDLE_SCHOOL, home_team_name='测试一队',
            away_team_name='对手队', home_team=self.team, match_time=timezone.now(),
        )
        MatchEvent.objects.create(
            match=match, type='goal', minute=10, athlete=self.p1, client_token='stage1-token',
        )
        resp = self.admin_client.delete(f'/api/admin/players/{self.p1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '球员有赛事记录，不可删除')


class PlayerAccountStatusTest(BaseAdminData):
    def test_disable_requires_reason_and_blocks_login(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p3.id}/account-status', {
            'action': 'disable',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('reason', resp.json()['msg'])

        player_client = auth_client(self.p3_user)
        resp = self.admin_client.put(f'/api/admin/players/{self.p3.id}/account-status', {
            'action': 'disable', 'reason': '账号存在异常需禁用',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(data['status'], UserStatus.DISABLED)
        self.assertFalse(data['is_active'])
        self.assertEqual(login(APIClient(), '13800000003').json()['code'], 20003)
        self.assertEqual(player_client.get('/api/auth/me').status_code, 401)

    def test_enable_restores_login(self):
        self.admin_client.put(f'/api/admin/players/{self.p3.id}/account-status', {
            'action': 'disable', 'reason': '账号存在异常需禁用',
        }, format='json')
        resp = self.admin_client.put(f'/api/admin/players/{self.p3.id}/account-status', {
            'action': 'enable',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['status'], UserStatus.ACTIVE)
        self.assertEqual(login(APIClient(), '13800000003').json()['code'], 0)

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p3.id}/account-status', {'action': 'bad'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)


class CoachWindowAndObjectionTest(BaseAdminData):
    def test_invalid_window_rejected(self):
        now = timezone.now()
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish', {
            'start_at': (now + timedelta(hours=1)).isoformat(),
            'end_at': now.isoformat(),
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertEqual(resp.json()['msg'], 'end_at: 公示结束时间必须晚于开始时间')

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish', {
            'end_at': (now + timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('start_at', resp.json()['msg'])

    def test_window_persisted_and_expired_treated_unpublished(self):
        now = timezone.now()
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish', {
            'start_at': (now + timedelta(hours=1)).isoformat(),
            'end_at': (now + timedelta(hours=2)).isoformat(),
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        detail = self.admin_client.get(f'/api/admin/coaches/{self.c1.id}').json()['data']
        self.assertEqual(
            detail['public_end_at'],
            timezone.localtime(now + timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
        )

        # 先取消公示，再演示「过期公示视为未公示」
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/unpublish', {
            'reason': '公示期设置错误需重设',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish', {
            'start_at': (now - timedelta(hours=2)).isoformat(),
            'end_at': (now - timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        item = [row for row in self.admin_client.get('/api/admin/coaches').json()['data']['list']
                if row['id'] == self.c1.id][0]
        self.assertFalse(item['is_public'])

        # 过期后允许再次设置新的公示期
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/publish', {
            'start_at': now.isoformat(),
            'end_at': (now + timedelta(hours=3)).isoformat(),
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)

    def test_unpublish_reason_persisted(self):
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/unpublish', {
            'reason': '公示材料存在异议',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        detail = self.admin_client.get(f'/api/admin/coaches/{self.c3.id}').json()['data']
        self.assertEqual(detail['unpublish_reason'], '公示材料存在异议')

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/unpublish', {
            'reason': '短',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 10001)

    def test_objection_create_list_and_handle(self):
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/objections', {
            'submitter_name': '王五', 'contact': '13600000010',
            'content': '对公示结果有异议',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        obj = resp.json()['data']
        self.assertEqual(obj['coach_id'], self.c1.id)
        self.assertFalse(obj['handled'])

        data = self.admin_client.get(f'/api/admin/coaches/{self.c1.id}/objections').json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(set(data['list'][0]), {
            'id', 'coach_id', 'submitter_name', 'contact', 'content', 'handled', 'created_at',
        })
        data = self.admin_client.get(
            f'/api/admin/coaches/{self.c1.id}/objections', {'handled': 'false'},
        ).json()['data']
        self.assertEqual(data['total'], 1)

        resp = self.admin_client.put(f'/api/admin/objections/{obj["id"]}/handle', {
            'note': '已电话核实',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertTrue(resp.json()['data']['handled'])
        self.assertEqual(resp.json()['data']['note'], '已电话核实')
        self.assertTrue(
            AdminOperationLog.objects.filter(action='处理公示异议', target_id=self.c1.id).exists(),
        )

        resp = self.admin_client.put(f'/api/admin/objections/{obj["id"]}/handle', {}, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该异议已处理')

        data = self.admin_client.get(
            f'/api/admin/coaches/{self.c1.id}/objections', {'handled': 'true'},
        ).json()['data']
        self.assertEqual(data['total'], 1)

    def test_objection_missing_and_required_fields(self):
        resp = self.admin_client.post('/api/admin/coaches/999999/objections', {
            'submitter_name': '王五', 'contact': '13600000010', 'content': '异议',
        }, format='json')
        self.assertEqual(resp.json()['code'], 30001)
        self.assertEqual(resp.json()['msg'], '教练员不存在')

        resp = self.admin_client.put('/api/admin/objections/999999/handle', {}, format='json')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['msg'], '异议不存在')

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c1.id}/objections', {
            'submitter_name': '王五', 'contact': '13600000010',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('content', resp.json()['msg'])


class CoachBatchAndRestoreTest(BaseAdminData):
    def test_batch_reset_coach_password(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/coaches/{self.c3.id}').json()['code'], 0,
        )
        resp = self.admin_client.post('/api/admin/coaches/batch-reset-password', {
            'ids': [self.c1.id, self.c2.id, self.c3.id],
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual((data['total'], data['success']), (3, 2))
        self.assertEqual(data['skipped'][0]['id'], self.c3.id)

        self.coach_user.refresh_from_db()
        self.assertTrue(check_password(settings.DEFAULT_RESET_PASSWORD, self.coach_user.password))

    def test_restore_coach_reactivates_account(self):
        self.assertEqual(
            self.admin_client.delete(f'/api/admin/coaches/{self.c3.id}').json()['code'], 0,
        )
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/restore')
        self.assertEqual(resp.json()['code'], 0)
        self.c3.refresh_from_db()
        self.coach3_user.refresh_from_db()
        self.assertIsNone(self.c3.deleted_at)
        self.assertTrue(self.coach3_user.is_active)
        self.assertEqual(login(APIClient(), '13700000003').json()['code'], 0)

        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/restore')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该教练员未在归档中')

    def test_coach_delete_blocked_by_registration_and_match(self):
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        competition = Competition.objects.create(name='教练赛事', year=2026)
        Registration.objects.create(
            competition=competition, team=self.team, status=Registration.Status.SUBMITTED,
        )
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '教练所在球队有进行中的报名，不可删除')

        Registration.objects.all().delete()
        Match.objects.create(
            group_type=GroupType.MIDDLE_SCHOOL, home_team_name='测试一队',
            away_team_name='对手队', home_team=self.team, match_time=timezone.now(),
        )
        resp = self.admin_client.delete(f'/api/admin/coaches/{self.c1.id}')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '教练有赛事记录，不可删除')


# ===========================================================================
# 阶段二扩展测试：停赛联动 / 归属变更 / 合并球员
# ===========================================================================

def add_card_events(player, yellow=0, red=0):
    """给球员造红黄牌事件（同场赛事，client_token 唯一）。"""
    match = Match.objects.create(
        group_type=GroupType.MIDDLE_SCHOOL, home_team_name='测试一队',
        away_team_name='对手队', match_time=timezone.now(),
    )
    events = []
    for kind, count in (('yellow', yellow), ('red', red)):
        for index in range(count):
            events.append(MatchEvent.objects.create(
                match=match, type=kind, minute=index + 1, athlete=player,
                client_token=uuid4().hex,
            ))
    return events


class SuspensionTest(BaseAdminData):
    def test_preview_yellow_threshold(self):
        add_card_events(self.p3, yellow=4)
        resp = self.admin_client.get(f'/api/admin/players/{self.p3.id}/suspension-preview')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertEqual(set(data), {
            'yellow_total', 'red_total', 'yellow_since_last_ban', 'will_suspend', 'rule',
        })
        self.assertEqual(data['yellow_total'], 4)
        self.assertEqual(data['yellow_since_last_ban'], 4)
        self.assertTrue(data['will_suspend'])
        self.assertEqual(data['rule'], {
            'yellow_threshold': 4, 'yellow_ban_matches': 1, 'red_ban_matches': 2,
        })

    def test_preview_below_threshold(self):
        add_card_events(self.p3, yellow=3)
        data = self.admin_client.get(
            f'/api/admin/players/{self.p3.id}/suspension-preview',
        ).json()['data']
        self.assertEqual(data['yellow_since_last_ban'], 3)
        self.assertFalse(data['will_suspend'])

    def test_generate_yellow_and_idempotent(self):
        add_card_events(self.p3, yellow=4)
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/suspensions/generate')
        self.assertEqual(resp.json()['code'], 0)
        created = resp.json()['data']['created']
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]['source'], PlayerSuspension.Source.AUTO)
        self.assertEqual(created[0]['matches_count'], 1)
        self.assertEqual(created[0]['status'], PlayerSuspension.Status.ACTIVE)
        self.assertEqual(len(created[0]['source_events']), 4)

        self.p3.refresh_from_db()
        self.assertEqual(self.p3.eligibility_status, 'suspended')
        detail = self.admin_client.get(f'/api/admin/players/{self.p3.id}').json()['data']
        self.assertEqual(detail['suspensions']['active_count'], 1)
        self.assertIsNotNone(detail['suspensions']['latest'])

        # 幂等：已计入的事件不重复生成
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/suspensions/generate')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '未达到停赛条件')
        self.assertEqual(PlayerSuspension.objects.filter(player=self.p3).count(), 1)

    def test_generate_red_card_two_matches(self):
        add_card_events(self.p3, red=1)
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/suspensions/generate')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['created'][0]['matches_count'], 2)

    def test_generate_without_cards_rejected(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p3.id}/suspensions/generate')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '未达到停赛条件')

    def test_manual_suspend_and_revoke(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '暴力犯规追加停赛', 'matches_count': 3,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        record = resp.json()['data']
        self.assertEqual(record['source'], PlayerSuspension.Source.MANUAL)
        self.assertEqual(record['status'], PlayerSuspension.Status.ACTIVE)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'suspended')

        resp = self.admin_client.put(f'/api/admin/suspensions/{record["id"]}/revoke', {
            'reason': '申诉成功撤销停赛',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['status'], PlayerSuspension.Status.REVOKED)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'eligible')

        resp = self.admin_client.put(f'/api/admin/suspensions/{record["id"]}/revoke', {
            'reason': '重复撤销',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该停赛记录已结束')

        resp = self.admin_client.put('/api/admin/suspensions/999999/revoke', {
            'reason': '不存在',
        }, format='json')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['msg'], '停赛记录不存在')

    def test_manual_requires_reason_and_matches(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '停赛',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('matches_count', resp.json()['msg'])

        resp = self.admin_client.put('/api/admin/suspensions/1/revoke', {}, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('reason', resp.json()['msg'])


class PlayerTeamTest(BaseAdminData):
    def test_bind_team_writes_change(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}/team', {
            'team_id': self.team.id, 'reason': '首次绑定球队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertIsNone(resp.json()['data']['from_team_id'])
        self.assertEqual(resp.json()['data']['to_team_id'], self.team.id)

        self.p2.refresh_from_db()
        self.assertEqual(self.p2.team_id, self.team.id)
        change = PlayerTeamChange.objects.get(player=self.p2)
        self.assertEqual(change.to_team_id, self.team.id)
        self.assertEqual(change.reason, '首次绑定球队')
        self.assertEqual(change.changed_by, self.admin.id)

        data = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/team-changes',
        ).json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(set(data['list'][0]), {
            'id', 'player_id', 'from_team_id', 'from_team_name', 'to_team_id',
            'to_team_name', 'reason', 'changed_by', 'created_at',
        })

    def test_unbind_and_invalid_team(self):
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/team', {
            'team_id': None, 'reason': '球员离队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['from_team_id'], self.team.id)
        self.assertIsNone(resp.json()['data']['to_team_id'])
        self.p1.refresh_from_db()
        self.assertIsNone(self.p1.team_id)

        rejected = Team.objects.create(
            school=self.school, coach=self.coach_user, name='驳回队',
            group_type=GroupType.MIDDLE_SCHOOL, status=TeamStatus.REJECTED,
        )
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}/team', {
            'team_id': rejected.id, 'reason': '绑定驳回球队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '仅可绑定待审核或已通过的球队')

        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}/team', {
            'team_id': 999999, 'reason': '绑定不存在球队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)

    def test_team_change_required_fields(self):
        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}/team', {'team_id': self.team.id}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('reason', resp.json()['msg'])

        resp = self.admin_client.put(
            f'/api/admin/players/{self.p2.id}/team', {'reason': '缺少球队'}, format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('team_id', resp.json()['msg'])


class MergePlayerTest(BaseAdminData):
    def _create_duplicate(self, name='张三', phone='13600000021'):
        resp = self.admin_client.post('/api/admin/players', {
            'name': name, 'phone': phone, 'id_card': NEW_ID_CARD_1,
            'gender': 'female', 'grade': '初三',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        return AthleteProfile.objects.get(pk=resp.json()['data']['id'])

    def test_merge_validation(self):
        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': self.p1.id, 'reason': '自合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '不能合并同一条档案')

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': self.p2.id, 'reason': '信息不同',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '两人姓名或身份证号需至少一项相同')

        duplicate = self._create_duplicate()
        AthleteProfile.objects.filter(pk=duplicate.pk).update(team=self.team)
        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': duplicate.id, 'reason': '都在队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '两人均在有效球队中，请先调整')

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': duplicate.id,
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('reason', resp.json()['msg'])

    def test_merge_migrates_relations_and_fills_fields(self):
        duplicate = self._create_duplicate()
        add_card_events(duplicate, yellow=1)
        PlayerTeamChange.objects.create(
            player=duplicate, from_team=None, to_team=None, reason='历史解绑',
            changed_by=self.admin.id,
        )
        resp = self.admin_client.post(
            f'/api/admin/players/{duplicate.id}/suspensions',
            {'reason': '人工停赛', 'matches_count': 2}, format='json',
        )
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': duplicate.id, 'reason': '重复档案合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        data = resp.json()['data']
        self.assertTrue(data['merged'])
        self.assertEqual(data['primary_id'], self.p1.id)
        self.assertEqual(data['duplicate_id'], duplicate.id)
        self.assertIn('grade', data['filled_fields'])
        self.assertIn('gender', data['filled_fields'])

        # 关联数据迁移到主档案
        self.assertEqual(MatchEvent.objects.filter(athlete=duplicate).count(), 0)
        self.assertEqual(MatchEvent.objects.filter(athlete=self.p1).count(), 1)
        self.assertEqual(PlayerTeamChange.objects.filter(player=duplicate).count(), 0)
        self.assertEqual(PlayerTeamChange.objects.filter(player=self.p1).count(), 1)
        self.assertEqual(PlayerSuspension.objects.filter(player=duplicate).count(), 0)
        self.assertEqual(PlayerSuspension.objects.filter(player=self.p1).count(), 1)

        self.p1.refresh_from_db()
        self.assertEqual(self.p1.grade, '初三')
        self.assertEqual(self.p1.gender, 'female')

        # 重复档案软删 + 账号禁用
        duplicate.refresh_from_db()
        self.assertIsNotNone(duplicate.deleted_at)
        dup_user = duplicate.user
        dup_user.refresh_from_db()
        self.assertFalse(dup_user.is_active)
        self.assertEqual(
            login(APIClient(), '13600000021', settings.DEFAULT_RESET_PASSWORD).json()['code'],
            20003,
        )
        self.assertTrue(
            AdminOperationLog.objects.filter(action='合并球员', target_id=self.p1.id).exists(),
        )

    def test_merge_same_id_card_allowed(self):
        duplicate = self._create_duplicate(name='李雷')
        AthleteProfile.objects.filter(pk=duplicate.pk).update(id_card=ID_CARD_1)
        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': duplicate.id, 'reason': '身份证重复',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)


# ===========================================================================
# 阶段二修复测试：停赛资格保护 / 合并继承球队 / 停赛列表
# ===========================================================================

class SuspensionEligibilityGuardTest(BaseAdminData):
    def _disqualify(self, pk):
        resp = self.admin_client.put(f'/api/admin/players/{pk}/status-flags', {
            'eligibility_status': 'disqualified', 'reason': '资格审查造假属实',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)

    def test_manual_suspension_keeps_disqualified(self):
        self._disqualify(self.p2.id)
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '追加停赛', 'matches_count': 2,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data']['matches_count'], 2)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'disqualified')

        record = PlayerSuspension.objects.get(player=self.p2)
        self.assertIsNone(record.prev_eligibility)

    def test_revoke_restores_disqualified(self):
        self._disqualify(self.p2.id)
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '追加停赛', 'matches_count': 2,
        }, format='json')
        sid = resp.json()['data']['id']
        resp = self.admin_client.put(f'/api/admin/suspensions/{sid}/revoke', {
            'reason': '撤销',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'disqualified')

    def test_normal_suspend_and_revoke_restores_eligible(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '人工停赛', 'matches_count': 1,
        }, format='json')
        sid = resp.json()['data']['id']
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'suspended')
        resp = self.admin_client.put(f'/api/admin/suspensions/{sid}/revoke', {
            'reason': '撤销',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'eligible')

    def test_partial_revoke_keeps_suspended(self):
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '停赛1', 'matches_count': 1,
        }, format='json')
        sid1 = resp.json()['data']['id']
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '停赛2', 'matches_count': 1,
        }, format='json')
        sid2 = resp.json()['data']['id']

        resp = self.admin_client.put(f'/api/admin/suspensions/{sid1}/revoke', {
            'reason': '撤销第一条',
        }, format='json')
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'suspended')

        resp = self.admin_client.put(f'/api/admin/suspensions/{sid2}/revoke', {
            'reason': '撤销第二条',
        }, format='json')
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'eligible')

    def test_auto_suspension_keeps_disqualified(self):
        self._disqualify(self.p2.id)
        add_card_events(self.p2, yellow=4)
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions/generate')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'disqualified')
        record = PlayerSuspension.objects.get(player=self.p2)
        self.assertIsNone(record.prev_eligibility)


class SuspensionListTest(BaseAdminData):
    def _create_records(self):
        self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '停赛1', 'matches_count': 1,
        }, format='json')
        resp = self.admin_client.post(f'/api/admin/players/{self.p2.id}/suspensions', {
            'reason': '停赛2', 'matches_count': 1,
        }, format='json')
        sid2 = resp.json()['data']['id']
        self.admin_client.put(f'/api/admin/suspensions/{sid2}/revoke', {
            'reason': '撤销第二条',
        }, format='json')

    def test_list_all_and_order(self):
        self._create_records()
        data = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/suspensions',
        ).json()['data']
        self.assertEqual(data['total'], 2)
        self.assertGreater(data['list'][0]['id'], data['list'][1]['id'])
        self.assertEqual(set(data['list'][0]), {
            'id', 'player_id', 'reason', 'matches_count', 'start_at', 'end_at',
            'source', 'source_events', 'status', 'created_by', 'created_at',
        })

    def test_filter_by_status(self):
        self._create_records()
        data = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/suspensions', {'status': 'active'},
        ).json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['status'], 'active')

        data = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/suspensions', {'status': 'revoked'},
        ).json()['data']
        self.assertEqual(data['total'], 1)

    def test_invalid_status_returns_10001(self):
        resp = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/suspensions', {'status': 'xx'},
        )
        self.assertEqual(resp.json()['code'], 10001)

    def test_page_overflow_returns_real_page(self):
        self._create_records()
        data = self.admin_client.get(
            f'/api/admin/players/{self.p2.id}/suspensions',
            {'page': 99, 'page_size': 1},
        ).json()['data']
        self.assertEqual(data['page'], 2)
        self.assertEqual(len(data['list']), 1)

    def test_missing_player_30001(self):
        resp = self.admin_client.get('/api/admin/players/999999/suspensions')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['code'], 30001)
        self.assertEqual(resp.json()['msg'], '球员不存在')


class MergeTeamInheritTest(BaseAdminData):
    def _create_duplicate(self, name='王五', phone='13600000022'):
        resp = self.admin_client.post('/api/admin/players', {
            'name': name, 'phone': phone, 'id_card': NEW_ID_CARD_2,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        return AthleteProfile.objects.get(pk=resp.json()['data']['id'])

    def test_merge_inherits_team_and_writes_change(self):
        duplicate = self._create_duplicate()
        AthleteProfile.objects.filter(pk=duplicate.pk).update(team=self.team)

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '重复档案合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.team_id, self.team.id)

        change = PlayerTeamChange.objects.get(player=self.p2)
        self.assertIsNone(change.from_team_id)
        self.assertEqual(change.to_team_id, self.team.id)
        self.assertEqual(change.reason, '合并档案继承')
        self.assertEqual(change.changed_by, self.admin.id)

    def test_merge_primary_has_team_no_change(self):
        duplicate = self._create_duplicate(name='张三', phone='13600000023')
        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p1.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.team_id, self.team.id)
        self.assertEqual(PlayerTeamChange.objects.filter(player=self.p1).count(), 0)

    def test_merge_both_no_team(self):
        duplicate = self._create_duplicate()
        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertIsNone(self.p2.team_id)
        self.assertEqual(PlayerTeamChange.objects.filter(player=self.p2).count(), 0)

    def test_merge_inherit_rejected_team_skipped(self):
        duplicate = self._create_duplicate()
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.REJECTED)
        AthleteProfile.objects.filter(pk=duplicate.pk).update(team=self.team)

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertIsNone(self.p2.team_id)
        self.assertEqual(PlayerTeamChange.objects.filter(player=self.p2).count(), 0)

    def test_merge_inherit_pending_team_ok(self):
        duplicate = self._create_duplicate()
        Team.objects.filter(pk=self.team.pk).update(status=TeamStatus.PENDING)
        AthleteProfile.objects.filter(pk=duplicate.pk).update(team=self.team)

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.team_id, self.team.id)
        self.assertTrue(
            PlayerTeamChange.objects.filter(
                player=self.p2, to_team=self.team, reason='合并档案继承',
            ).exists(),
        )


# ===========================================================================
# 阶段二小瑕疵修复测试
# ===========================================================================

class MergeSuspensionSyncTest(BaseAdminData):
    def _create_duplicate(self, name='王五', phone='13600000031'):
        resp = self.admin_client.post('/api/admin/players', {
            'name': name, 'phone': phone, 'id_card': NEW_ID_CARD_3,
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        return AthleteProfile.objects.get(pk=resp.json()['data']['id'])

    def test_merge_active_suspension_sets_primary_suspended(self):
        # 主档案 eligible，重复档案有 active 停赛
        duplicate = self._create_duplicate()
        resp = self.admin_client.post(
            f'/api/admin/players/{duplicate.id}/suspensions',
            {'reason': '重复档案停赛', 'matches_count': 2}, format='json',
        )
        sid = resp.json()['data']['id']

        # 确认主档案当前 eligible
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'eligible')

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)

        # 合并后主档案 eligibility_status 被置为 suspended
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'suspended')
        # 停赛记录已迁移到主档案
        self.assertTrue(
            PlayerSuspension.objects.filter(player=self.p2, pk=sid).exists(),
        )
        self.assertFalse(PlayerSuspension.objects.filter(player=duplicate).exists())

        # 撤销后恢复
        resp = self.admin_client.put(f'/api/admin/suspensions/{sid}/revoke', {
            'reason': '撤销',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'eligible')

    def test_merge_active_suspension_does_not_override_disqualified(self):
        duplicate = self._create_duplicate()
        resp = self.admin_client.post(
            f'/api/admin/players/{duplicate.id}/suspensions',
            {'reason': '重复档案停赛', 'matches_count': 1}, format='json',
        )

        # 主档案置为 disqualified
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}/status-flags', {
            'eligibility_status': 'disqualified', 'reason': '资格审查造假属实',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)

        resp = self.admin_client.post('/api/admin/players/merge', {
            'primary_id': self.p2.id, 'duplicate_id': duplicate.id, 'reason': '合并',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.eligibility_status, 'disqualified')
        # 停赛记录正常迁移过来
        self.assertEqual(
            PlayerSuspension.objects.filter(player=self.p2, status='active').count(), 1,
        )


class ChangeTeamNoiseTest(BaseAdminData):
    def test_same_team_change_blocked(self):
        before = PlayerTeamChange.objects.count()
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/team', {
            'team_id': self.team.id, 'reason': '换到同一队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已在该球队中')
        self.assertEqual(PlayerTeamChange.objects.count(), before)

    def test_unbind_empty_team_blocked(self):
        before = PlayerTeamChange.objects.count()
        resp = self.admin_client.put(f'/api/admin/players/{self.p2.id}/team', {
            'team_id': None, 'reason': '解绑无球队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertEqual(resp.json()['msg'], '该球员已在该球队中')
        self.assertEqual(PlayerTeamChange.objects.count(), before)

    def test_regular_move_and_unbind_unchanged(self):
        # 在 A 队换到 B 队（换队）
        team2 = Team.objects.create(
            school=self.school, coach=self.coach_user, name='测试二队',
            group_type=GroupType.MIDDLE_SCHOOL,
        )
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/team', {
            'team_id': team2.id, 'reason': '换队',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.team_id, team2.id)

        # 从 B 队解绑
        resp = self.admin_client.put(f'/api/admin/players/{self.p1.id}/team', {
            'team_id': None, 'reason': '解绑',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0)
        self.p1.refresh_from_db()
        self.assertIsNone(self.p1.team_id)
