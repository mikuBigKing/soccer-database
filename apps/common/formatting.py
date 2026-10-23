"""时间格式化工具：对齐 Laravel `toDateTimeString()` 的输出。"""
from django.utils import timezone

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'
DATE_FORMAT = '%Y-%m-%d'


def format_dt(value) -> str:
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    return timezone.localtime(value).strftime(DATETIME_FORMAT)


def format_date(value) -> str:
    if value is None:
        return None
    return value.strftime(DATE_FORMAT)
