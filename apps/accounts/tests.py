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
            username='13900000001', phone='13900000001', name='省级',
            role=UserRole.PROVINCE, password='pass1234',
        )
        ProvinceProfile.objects.create(user=self.province_user, name='省级', province=self.province)
        self.city_user = User.objects.create_user(
            username='13900000002', phone='13900000002', name='市级',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=self.city_user, name='市级', city=self.city)
        self.athlete_user = User.objects.create_user(
            username='13900000003', phone='13900000003', name='球员',
            role=UserRole.ATHLETE, password='pass1234',
        )
        AthleteProfile.objects.create(user=self.athlete_user, name='球员', status='approved')

    def auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token(user)}')

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
        resp = self.client.post(
            '/api/auth/password',
            {'old_password': 'pass1234', 'new_password': 'newpass99'},
            format='json',
        )
        self.assertEqual(resp.json()['code'], 0)
        self.assertFalse(self.client.post(
            '/api/auth/login', {'phone': '13900000003', 'password': 'pass1234'}, format='json'
        ).json()['success'])
        self.assertTrue(self.client.post(
            '/api/auth/login', {'phone': '13900000003', 'password': 'newpass99'}, format='json'
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


