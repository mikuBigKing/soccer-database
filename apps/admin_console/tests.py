"""管理端球员 / 教练员 14 个接口测试（正常流 + 异常流）。"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, CoachProfile, ProfileStatus, UserStatus
from apps.admin_console.models import AdminOperationLog
from apps.org.models import City, GroupType, Province, School, Team, TeamStatus

User = get_user_model()

ID_CARD_1 = '11010519491231002X'
ID_CARD_2 = '110101199003077758'
ID_CARD_3 = '110101199003077766'
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
        item = self.admin_client.get('/api/admin/players').json()['data']['list'][0]
        self.assertEqual(set(item), {
            'id', 'name', 'jersey_no', 'position', 'team_id', 'team_name',
            'group_type', 'status', 'is_public', 'avatar', 'created_at',
        })

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
            'id', 'name', 'jersey_no', 'position', 'team_id', 'team_name', 'group_type',
            'status', 'is_public', 'avatar', 'created_at', 'id_card', 'phone',
            'enrollment_cert', 'reject_reason', 'updated_at',
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
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/unpublish')
        self.assertEqual(resp.json()['code'], 0)
        self.assertEqual(resp.json()['data'], {'id': self.c3.id, 'is_public': False})
        resp = self.admin_client.post(f'/api/admin/coaches/{self.c3.id}/unpublish')
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
