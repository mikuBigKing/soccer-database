"""报名账号权限下发接口单元测试。

覆盖场景：
- 管理员可成功设置 permissions
- 非管理员（省级/市级）设置 permissions 被拒
- permissions 正确落库且可清空
- 账号序列化响应不暴露 permissions 字段
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import CityProfile, ProvinceProfile, UserRole
from apps.org.models import City, Province

User = get_user_model()


def _token(user) -> str:
    return str(RefreshToken.for_user(user).access_token)


class AccountPermissionsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.province = Province.objects.create(name='测试省', code='100000')
        self.city = City.objects.create(province=self.province, name='测试市', code='100100')

        # 管理员：有权限下发权限点
        self.admin = User.objects.create_user(
            username='admin@test.com', email='admin@test.com', name='管理员',
            role=UserRole.ADMIN, password='pass1234',
        )
        # 省级账号：可访问账号接口，但不是 admin，无权下发权限点
        self.prov_user = User.objects.create_user(
            username='13800000001', phone='13800000001', name='省级账号',
            role=UserRole.PROVINCE, password='pass1234',
        )
        ProvinceProfile.objects.create(user=self.prov_user, name='省级账号', province=self.province)
        # 市级账号：被操作目标
        self.city_user = User.objects.create_user(
            username='13800000002', phone='13800000002', name='市级账号',
            role=UserRole.CITY, password='pass1234',
        )
        CityProfile.objects.create(user=self.city_user, name='市级账号', city=self.city)

        self.url = f'/api/province/accounts/{self.city_user.pk}'

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(user)}')

    def test_admin_can_set_permissions(self):
        """管理员可成功设置权限点。"""
        self._auth(self.admin)
        resp = self.client.put(self.url, {'permissions': ['live:match:view', 'registration:export']}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.city_user.refresh_from_db()
        self.assertEqual(self.city_user.permissions, ['live:match:view', 'registration:export'])

    def test_non_admin_cannot_set_permissions(self):
        """省级账号不是 admin，设置权限点应被拒（业务码 FORBIDDEN=20004）。"""
        self._auth(self.prov_user)
        resp = self.client.put(self.url, {'permissions': ['live:match:view']}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 20004)

        self.city_user.refresh_from_db()
        self.assertEqual(self.city_user.permissions, [])

    def test_clear_permissions_with_empty_list(self):
        """管理员传空列表可清空权限点。"""
        self.city_user.permissions = ['live:match:view']
        self.city_user.save()

        self._auth(self.admin)
        resp = self.client.put(self.url, {'permissions': []}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.city_user.refresh_from_db()
        self.assertEqual(self.city_user.permissions, [])

    def test_response_does_not_expose_permissions(self):
        """账号详情响应不应包含 permissions 字段（避免权限点泄露）。"""
        self._auth(self.admin)
        self.city_user.permissions = ['secret:perm']
        self.city_user.save()

        resp = self.client.put(self.url, {'name': '新名字'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data.get('data', resp.data)
        self.assertNotIn('permissions', data)

    def test_admin_can_set_permissions_and_name_together(self):
        """管理员可同时修改基础信息与权限点。"""
        self._auth(self.admin)
        resp = self.client.put(
            self.url,
            {'name': '改名市州', 'permissions': ['live:match:view']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.city_user.refresh_from_db()
        self.assertEqual(self.city_user.name, '改名市州')
        self.assertEqual(self.city_user.permissions, ['live:match:view'])
