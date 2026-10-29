"""管理端球员 / 教练员的查询与修改序列化器。

错误文案按契约文档：`字段名: 原因` 由全局异常处理拼接，
因此这里只负责字段级文案。
"""
from rest_framework import serializers

from apps.accounts.models import ProfileStatus
from apps.org.models import GroupType

ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CARD_CHECK_CODES = '10X98765432'
PHONE_REGEX = r'^1[3-9]\d{9}$'

INT_ERRORS = {'invalid': '必须为整数'}
CHOICE_ERRORS = {'invalid_choice': '取值不合法'}


class QueryBooleanField(serializers.BooleanField):
    """查询参数用布尔字段。

    QueryDict 属于 HTML 输入，DRF 在字段缺省时会返回 default_empty_html（False），
    导致「未传 is_public」被当成 false 过滤；这里改成 empty，保持「未传即不过滤」。
    """

    default_empty_html = serializers.empty


def validate_id_card(value: str) -> str:
    """18 位身份证 + 校验位。"""
    value = (value or '').strip().upper()
    if len(value) != 18 or not value[:17].isdigit() or value[17] not in '0123456789X':
        raise serializers.ValidationError('身份证号格式不正确')
    total = sum(int(char) * weight for char, weight in zip(value[:17], ID_CARD_WEIGHTS))
    if ID_CARD_CHECK_CODES[total % 11] != value[17]:
        raise serializers.ValidationError('身份证号校验位不正确')
    return value


class PlayerQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    page_size = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    keyword = serializers.CharField(required=False, allow_blank=True, max_length=50)
    name = serializers.CharField(required=False, allow_blank=True, max_length=50)
    id_card = serializers.CharField(required=False, allow_blank=True, max_length=18)
    jersey_no = serializers.IntegerField(
        required=False, min_value=0, max_value=99,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–99', 'max_value': '取值范围 0–99'},
    )
    team_id = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    group_type = serializers.ChoiceField(required=False, choices=GroupType.choices, error_messages=CHOICE_ERRORS)
    status = serializers.ChoiceField(required=False, choices=ProfileStatus.choices, error_messages=CHOICE_ERRORS)

    is_public = QueryBooleanField(required=False, error_messages={'invalid': '取值不合法'})

    def validate_id_card(self, value):
        # 归一化大小写，便于 `id_card__iexact` 精确匹配；搜索参数保持宽松，不校验 18 位
        return (value or '').strip().upper()


class CoachQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    page_size = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    keyword = serializers.CharField(required=False, allow_blank=True, max_length=50)
    name = serializers.CharField(required=False, allow_blank=True, max_length=50)
    id_card = serializers.CharField(required=False, allow_blank=True, max_length=18)
    team_id = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    group_type = serializers.ChoiceField(required=False, choices=GroupType.choices, error_messages=CHOICE_ERRORS)
    status = serializers.ChoiceField(required=False, choices=ProfileStatus.choices, error_messages=CHOICE_ERRORS)
    is_public = QueryBooleanField(required=False, error_messages={'invalid': '取值不合法'})

    def validate_id_card(self, value):
        # 归一化大小写，便于 `id_card__iexact` 精确匹配；搜索参数保持宽松，不校验 18 位
        return (value or '').strip().upper()


class PlayerUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=50)
    jersey_no = serializers.IntegerField(
        required=False, min_value=0, max_value=99,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–99', 'max_value': '取值范围 0–99'},
    )
    position = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    avatar = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone = serializers.RegexField(
        PHONE_REGEX, required=False, error_messages={'invalid': '手机号格式不正确'},
    )
    id_card = serializers.CharField(required=False, max_length=18)

    def validate_id_card(self, value):
        return validate_id_card(value)


class CoachUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=50)
    phone = serializers.RegexField(
        PHONE_REGEX, required=False, max_length=20,
        error_messages={'invalid': '手机号格式不正确'},
    )
    id_card = serializers.CharField(required=False, max_length=18)
    qualification = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_id_card(self, value):
        return validate_id_card(value)
