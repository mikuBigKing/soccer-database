"""认证与账号安全回归测试。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import AthleteProfile, CityProfile, ProvinceProfile, UserRole
from apps.org.models import City, Province

User = get_user_model()


def access_token(user):
    return str(RefreshToken.for_user(user).access_token)


class AuthSecurityTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='910000')
        self.city = City.objects.create(province=self.province, name='测试市', code='910100')
        self.admin = User.objects.create_user(
            username='admin@test.com', email='admin@test.com', name='管理员',
            role=UserRole.ADMIN, password='pass1234',
        )
        self.province_user = User.objects.create_user(
            username='province@test.com', email='province@test.com', phone='13900000001', name='省级',
            role=UserRole.PROVINCE, password='pass1234',
        )
        ProvinceProfile.objects.create(user=self.province_user, name='省级', province=self.province)
        self.city_user = User.objects.create_user(
            username='city@test.com', email='city@test.com', phone='13900000002', name='市级',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=self.city_user, name='市级', city=self.city)
        self.athlete_user = User.objects.create_user(
            username='athlete@test.com', email='athlete@test.com', phone='13900000003', name='球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        AthleteProfile.objects.create(user=self.athlete_user, name='球员', status='approved')

    def auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(user)}')

    def send_code(self, email, scene):
        """固定验证码为 123456，避免依赖邮件内容。"""
        from unittest.mock import patch

        with patch('apps.accounts.verification._generate_code', return_value='123456'):
            resp = self.client.post('/api/auth/email/send',
                                    {'email': email, 'scene': scene}, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())
        return '123456'

    def test_disabled_account_token_is_rejected(self):
        self.auth(self.athlete_user)
        self.assertEqual(self.client.get('/api/athlete/profile').status_code, 200)

        self.athlete_user.status = 'disabled'
        self.athlete_user.save(update_fields=['status'])
        resp = self.client.get('/api/athlete/profile')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 20003)

    def test_refresh_endpoint_issues_new_access(self):
        refresh = str(RefreshToken.for_user(self.athlete_user))
        resp = self.client.post('/api/auth/refresh', {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['code'], 0)
        access = resp.json()['data']['access']
        me = self.client.get('/api/auth/me', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['data']['role'], 'athlete')

    def test_refresh_with_invalid_token_returns_20001(self):
        resp = self.client.post('/api/auth/refresh', {'refresh': 'not-a-token'}, format='json')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['code'], 20001)
        self.assertIn('登录已过期', resp.json()['msg'])

    def test_logout_cannot_blacklist_other_users_token(self):
        victim_refresh = str(RefreshToken.for_user(self.athlete_user))
        self.auth(self.admin)
        resp = self.client.post('/api/auth/logout', {'refresh': victim_refresh}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 20004)
        # 他人 refresh 仍然可用
        again = self.client.post('/api/auth/refresh', {'refresh': victim_refresh}, format='json')
        self.assertEqual(again.status_code, 200)

    def test_logout_own_refresh(self):
        own_refresh = str(RefreshToken.for_user(self.athlete_user))
        self.auth(self.athlete_user)
        resp = self.client.post('/api/auth/logout', {'refresh': own_refresh}, format='json')
        self.assertEqual(resp.json()['code'], 0)
        refreshed = self.client.post('/api/auth/refresh', {'refresh': own_refresh}, format='json')
        self.assertEqual(refreshed.status_code, 401)

    def test_change_password(self):
        self.auth(self.athlete_user)
        code = self.send_code('athlete@test.com', 'change_password')
        resp = self.client.post(
            '/api/auth/password',
            {'old_password': 'pass1234', 'new_password': 'newpass99', 'code': code},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.assertFalse(self.client.post(
            '/api/auth/login', {'email': 'athlete@test.com', 'password': 'pass1234'}, format='json'
        ).json()['success'])
        self.assertTrue(self.client.post(
            '/api/auth/login', {'email': 'athlete@test.com', 'password': 'newpass99'}, format='json'
        ).json()['success'])

    def test_invalid_jwt_maps_to_20001_with_chinese_message(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer broken-token')
        resp = self.client.get('/api/auth/me')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['code'], 20001)
        self.assertEqual(resp.json()['msg'], '未登录或登录已过期')

    def test_public_endpoint_ignores_stale_token(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer broken-token')
        resp = self.client.get('/api/public/live/matches')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['code'], 0)

    def test_province_account_list_excludes_admin(self):
        self.auth(self.province_user)
        resp = self.client.get('/api/province/accounts')
        roles = [row['role'] for row in resp.json()['data']['list']]
        self.assertNotIn('admin', roles)
        self.assertIn('city', roles)

    def test_update_duplicate_phone_returns_param_error(self):
        self.auth(self.admin)
        resp = self.client.put(
            f'/api/province/accounts/{self.city_user.pk}',
            {'phone': '13900000001'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 10001)

    def test_update_invalid_city_returns_param_error(self):
        self.auth(self.admin)
        resp = self.client.put(
            f'/api/province/accounts/{self.city_user.pk}',
            {'city_id': 999999},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 10001)

    def test_cors_allows_configured_origin(self):
        resp = self.client.get('/api/health', HTTP_ORIGIN='http://localhost:8083')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Access-Control-Allow-Origin'], 'http://localhost:8083')


class EmailAuthTest(TestCase):
    """邮箱注册 / 找回密码 / 换绑邮箱 / 验证码风控。"""

    def setUp(self):
        self.client = APIClient()

    def send_code(self, email, scene):
        from unittest.mock import patch

        with patch('apps.accounts.verification._generate_code', return_value='123456'):
            resp = self.client.post('/api/auth/email/send',
                                    {'email': email, 'scene': scene}, format='json')
        return resp

    def test_register_with_email_code(self):
        self.assertEqual(self.send_code('new@test.com', 'register').json()['code'], 0)
        resp = self.client.post('/api/auth/register', {
            'name': '新球员', 'email': 'new@test.com', 'password': 'pass1234',
            'role': 'athlete', 'code': '123456',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())

        user = User.objects.get(email='new@test.com')
        self.assertEqual(user.username, 'new@test.com')
        self.assertIsNone(user.phone)
        self.assertTrue(user.check_password('pass1234'))

    def test_register_wrong_code_rejected(self):
        self.assertEqual(self.send_code('bad@test.com', 'register').json()['code'], 0)
        resp = self.client.post('/api/auth/register', {
            'name': '错码', 'email': 'bad@test.com', 'password': 'pass1234',
            'role': 'athlete', 'code': '000000',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertFalse(User.objects.filter(email='bad@test.com').exists())

    def test_register_existing_email_rejected_on_send(self):
        User.objects.create_user(username='taken@test.com', email='taken@test.com',
                                 name='已有', role=UserRole.ATHLETE, password='pass1234')
        resp = self.send_code('taken@test.com', 'register')
        self.assertEqual(resp.json()['code'], 10001)

    def test_send_code_cooldown(self):
        self.assertEqual(self.send_code('cool@test.com', 'register').json()['code'], 0)
        resp = self.send_code('cool@test.com', 'register')
        self.assertEqual(resp.json()['code'], 40001)
        self.assertIn('频繁', resp.json()['msg'])

    def test_password_reset(self):
        User.objects.create_user(username='reset@test.com', email='reset@test.com',
                                 name='待重置', role=UserRole.ATHLETE, password='pass1234')
        self.assertEqual(self.send_code('reset@test.com', 'reset_password').json()['code'], 0)
        resp = self.client.post('/api/auth/password/reset', {
            'email': 'reset@test.com', 'code': '123456', 'new_password': 'newpass99',
        }, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())

        login = self.client.post('/api/auth/login',
                                 {'email': 'reset@test.com', 'password': 'newpass99'}, format='json')
        self.assertEqual(login.json()['code'], 0, login.json())

    def test_reset_unknown_email_rejected(self):
        resp = self.send_code('nobody@test.com', 'reset_password')
        self.assertEqual(resp.json()['code'], 10001)

    def test_change_email_then_login_with_new_email(self):
        user = User.objects.create_user(username='old@test.com', email='old@test.com',
                                        name='换绑', role=UserRole.ATHLETE, password='pass1234')
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {access_token(user)}'
        )
        self.assertEqual(self.send_code('newmail@test.com', 'change_email').json()['code'], 0)
        resp = self.client.post('/api/auth/email/change',
                                {'email': 'newmail@test.com', 'code': '123456'}, format='json')
        self.assertEqual(resp.json()['code'], 0, resp.json())

        user.refresh_from_db()
        self.assertEqual(user.email, 'newmail@test.com')
        self.assertIsNotNone(user.email_verified_at)

        login = self.client.post('/api/auth/login',
                                 {'email': 'newmail@test.com', 'password': 'pass1234'}, format='json')
        self.assertEqual(login.json()['code'], 0, login.json())

    def test_placeholder_email_account_cannot_change_password(self):
        user = User.objects.create_user(username='ph@placeholder.local', email='ph@placeholder.local',
                                        name='占位', role=UserRole.ATHLETE, password='pass1234')
        AthleteProfile.objects.create(user=user, name='占位', status='approved')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(user)}')
        resp = self.client.post('/api/auth/password', {
            'old_password': 'pass1234', 'new_password': 'newpass99', 'code': '123456',
        }, format='json')
        self.assertEqual(resp.json()['code'], 10001)
        self.assertIn('未绑定', resp.json()['msg'])


