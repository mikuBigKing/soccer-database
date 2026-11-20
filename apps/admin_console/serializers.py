"""管理端球员 / 教练员的查询与修改序列化器。

错误文案按契约文档：`字段名: 原因` 由全局异常处理拼接，
因此这里只负责字段级文案。
"""
from django.conf import settings
from rest_framework import serializers

from apps.accounts.models import EligibilityStatus, Gender, ProfileStatus, VerifyStatus
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
    deleted = QueryBooleanField(required=False, error_messages={'invalid': '取值不合法'})

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
    deleted = QueryBooleanField(required=False, error_messages={'invalid': '取值不合法'})
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
    school_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, error_messages=INT_ERRORS,
    )
    gender = serializers.ChoiceField(
        required=False, allow_null=True, choices=Gender.choices, error_messages=CHOICE_ERRORS,
    )
    grade = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    height_cm = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=220,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–220', 'max_value': '取值范围 0–220'},
    )
    weight_kg = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=220,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–220', 'max_value': '取值范围 0–220'},
    )

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


class PlayerCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    phone = serializers.RegexField(PHONE_REGEX, error_messages={'invalid': '手机号格式不正确'})
    id_card = serializers.CharField(max_length=18)
    school_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, error_messages=INT_ERRORS,
    )
    grade = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    position = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    jersey_no = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=99,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–99', 'max_value': '取值范围 0–99'},
    )
    height_cm = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=220,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–220', 'max_value': '取值范围 0–220'},
    )
    weight_kg = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=220,
        error_messages={'invalid': '必须为整数', 'min_value': '取值范围 0–220', 'max_value': '取值范围 0–220'},
    )
    gender = serializers.ChoiceField(
        required=False, allow_null=True, choices=Gender.choices, error_messages=CHOICE_ERRORS,
    )

    def validate_id_card(self, value):
        return validate_id_card(value)


class PlayerStatusFlagsSerializer(serializers.Serializer):
    real_name_status = serializers.ChoiceField(
        required=False, choices=VerifyStatus.choices, error_messages=CHOICE_ERRORS,
    )
    enrollment_status = serializers.ChoiceField(
        required=False, choices=VerifyStatus.choices, error_messages=CHOICE_ERRORS,
    )
    eligibility_status = serializers.ChoiceField(
        required=False, choices=EligibilityStatus.choices, error_messages=CHOICE_ERRORS,
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate(self, attrs):
        status_fields = [
            key for key in ('real_name_status', 'enrollment_status', 'eligibility_status')
            if key in attrs
        ]
        if not status_fields:
            raise serializers.ValidationError('至少传一个状态字段')
        if attrs.get('eligibility_status') == EligibilityStatus.DISQUALIFIED:
            if len((attrs.get('reason') or '').strip()) < 5:
                raise serializers.ValidationError(
                    {'reason': '取消资格时必须填写不少于 5 个字的 reason'},
                )
        return attrs


class CoachPublishSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField(required=False, allow_null=True)
    end_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        start_at = attrs.get('start_at')
        end_at = attrs.get('end_at')
        if end_at and not start_at:
            raise serializers.ValidationError('传 end_at 时必须同时传 start_at')
        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError({'end_at': '公示结束时间必须晚于开始时间'})
        return attrs


class CoachUnpublishSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=5, max_length=200,
        error_messages={
            'required': '取消公示必须填写原因',
            'min_length': '取消失败原因不少于 5 个字',
            'max_length': '原因不超过 200 个字',
        },
    )


class CoachObjectionSerializer(serializers.Serializer):
    submitter_name = serializers.CharField(max_length=50)
    contact = serializers.CharField(max_length=50)
    content = serializers.CharField(max_length=2000)


class ObjectionQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    page_size = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    handled = QueryBooleanField(required=False, error_messages={'invalid': '取值不合法'})


class ObjectionHandleSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.RegexField(
        settings.PASSWORD_POLICY, required=False, max_length=64,
        error_messages={'invalid': '密码需 8-64 位且同时包含字母和数字'},
    )


class BatchResetPasswordSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1, error_messages=INT_ERRORS),
        min_length=1, max_length=100,
        error_messages={'min_length': 'ids 至少 1 个', 'max_length': 'ids 最多 100 个'},
    )
    password = serializers.RegexField(
        settings.PASSWORD_POLICY, required=False, max_length=64,
        error_messages={'invalid': '密码需 8-64 位且同时包含字母和数字'},
    )


class AccountStatusSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[('enable', '启用'), ('disable', '禁用')],
        error_messages={'invalid_choice': 'action 只能为 enable 或 disable'},
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate(self, attrs):
        if attrs['action'] == 'disable' and len((attrs.get('reason') or '').strip()) < 5:
            raise serializers.ValidationError({'reason': '禁用账号必须填写不少于 5 个字的 reason'})
        return attrs


class SuspensionManualSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)
    matches_count = serializers.IntegerField(
        min_value=1, max_value=100,
        error_messages={'invalid': '必须为整数', 'min_value': '停赛场次至少 1', 'max_value': '停赛场次最多 100'},
    )
    start_at = serializers.DateTimeField(required=False, allow_null=True)


class SuspensionRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=200,
        error_messages={'required': '撤销停赛必须填写原因', 'blank': '撤销停赛必须填写原因'},
    )


class PlayerTeamSerializer(serializers.Serializer):
    team_id = serializers.IntegerField(
        required=True, allow_null=True, min_value=1, error_messages=INT_ERRORS,
    )
    reason = serializers.CharField(max_length=200)


class MergePlayerSerializer(serializers.Serializer):
    primary_id = serializers.IntegerField(min_value=1, error_messages=INT_ERRORS)
    duplicate_id = serializers.IntegerField(min_value=1, error_messages=INT_ERRORS)
    reason = serializers.CharField(max_length=200)


class SuspensionQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    page_size = serializers.IntegerField(required=False, min_value=1, error_messages=INT_ERRORS)
    status = serializers.ChoiceField(
        required=False,
        choices=[('active', '生效中'), ('revoked', '已撤销'), ('expired', '已结束')],
        error_messages={'invalid_choice': '取值不合法'},
    )
