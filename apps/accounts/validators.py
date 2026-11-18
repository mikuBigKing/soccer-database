"""账号资料校验工具：身份证号格式与跨教练/球员档案唯一性。"""
import re

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

ID_CARD_PATTERN = re.compile(r'^\d{17}[\dXx]$')


def ensure_id_card_unique(value: str, athlete_pk: int = None, coach_pk: int = None) -> str:
    """校验身份证号格式与唯一性；空值直接返回 None。"""
    if not value:
        return None
    value = str(value).strip().upper()
    if not value:
        return None
    from .models import AthleteProfile, CoachProfile

    if not ID_CARD_PATTERN.match(value):
        raise BusinessException(ResponseCode.PARAM_INVALID, '身份证号格式不正确（18 位）')
    athletes = AthleteProfile.objects.filter(id_card=value)
    if athlete_pk:
        athletes = athletes.exclude(pk=athlete_pk)
    coaches = CoachProfile.objects.filter(id_card=value)
    if coach_pk:
        coaches = coaches.exclude(pk=coach_pk)
    if athletes.exists() or coaches.exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, '该身份证号已被其他账号使用')
    return value
