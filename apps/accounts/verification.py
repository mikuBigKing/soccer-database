"""邮箱验证码：签发、频控、校验与发送。

开发环境默认使用 console 邮件后端（验证码打印到控制台），
生产在 .env 配置 SMTP 即可切换，无需改代码。
"""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

from .models import User, VerificationCode
from .utils import normalize_email

logger = logging.getLogger(__name__)

TTL_SECONDS = getattr(settings, 'VERIFICATION_CODE_TTL_SECONDS', 600)
COOLDOWN_SECONDS = getattr(settings, 'VERIFICATION_CODE_COOLDOWN_SECONDS', 60)
HOURLY_LIMIT = getattr(settings, 'VERIFICATION_CODE_HOURLY_LIMIT', 5)
DAILY_LIMIT = getattr(settings, 'VERIFICATION_CODE_DAILY_LIMIT', 10)
MAX_ATTEMPTS = getattr(settings, 'VERIFICATION_CODE_MAX_ATTEMPTS', 5)

SCENE_SUBJECTS = {
    VerificationCode.Scene.REGISTER: '赛事系统注册验证码',
    VerificationCode.Scene.RESET_PASSWORD: '赛事系统找回密码验证码',
    VerificationCode.Scene.CHANGE_PASSWORD: '赛事系统修改密码验证码',
    VerificationCode.Scene.CHANGE_EMAIL: '赛事系统邮箱绑定验证码',
}


def _generate_code() -> str:
    return f'{secrets.randbelow(1000000):06d}'


def _hash_code(email: str, scene: str, code: str) -> str:
    payload = f'{settings.SECRET_KEY}|{email}|{scene}|{code}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _mask(email: str) -> str:
    name, _, domain = email.partition('@')
    if len(name) <= 2:
        masked = name[:1] + '*'
    else:
        masked = name[:2] + '***' + name[-1:]
    return f'{masked}@{domain}'


def _send_mail(email: str, scene: str, code: str) -> None:
    subject = SCENE_SUBJECTS.get(scene, '赛事系统验证码')
    minutes = max(1, TTL_SECONDS // 60)
    body = (
        f'您的验证码是 {code}，{minutes} 分钟内有效。\n'
        '请勿将验证码泄露给他人；如非本人操作请忽略本邮件。'
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


def issue_code(request, email: str, scene: str) -> dict:
    """签发并发送验证码；返回统一结果（不暴露邮箱是否存在）。"""
    email = normalize_email(email)
    if scene not in VerificationCode.Scene.values:
        raise BusinessException(ResponseCode.PARAM_INVALID, 'scene: 不支持的验证码场景')

    now = timezone.now()
    if scene == VerificationCode.Scene.REGISTER and User.objects.filter(email__iexact=email).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, '该邮箱已注册')
    if scene in (VerificationCode.Scene.RESET_PASSWORD, VerificationCode.Scene.CHANGE_PASSWORD):
        if not User.objects.filter(email__iexact=email).exists():
            raise BusinessException(ResponseCode.PARAM_INVALID, '该邮箱未注册')
    if scene == VerificationCode.Scene.CHANGE_EMAIL and User.objects.filter(email__iexact=email).exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, '该邮箱已被其他账号使用')

    recent = VerificationCode.objects.filter(email=email, scene=scene)
    last = recent.order_by('-id').first()
    if last and (now - last.created_at).total_seconds() < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - (now - last.created_at).total_seconds())
        raise BusinessException(ResponseCode.BUSINESS_ERROR, f'发送过于频繁，请 {wait} 秒后重试')
    if recent.filter(created_at__gte=now - timedelta(hours=1)).count() >= HOURLY_LIMIT:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该邮箱获取验证码过于频繁，请稍后再试')
    if recent.filter(created_at__gte=now - timedelta(days=1)).count() >= DAILY_LIMIT:
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '该邮箱今日验证码次数已达上限')

    # 旧码立即失效，避免多码并存
    recent.filter(used_at__isnull=True).update(used_at=now)

    code = _generate_code()
    record = VerificationCode.objects.create(
        email=email,
        scene=scene,
        code_hash=_hash_code(email, scene, code),
        expires_at=now + timedelta(seconds=TTL_SECONDS),
        ip=(request.META.get('REMOTE_ADDR') or '')[:64],
        trace_id=(getattr(request, 'trace_id', '') or '')[:64],
    )
    try:
        _send_mail(email, scene, code)
    except Exception:
        record.delete()
        logger.exception('verification mail send failed: %s', _mask(email))
        raise BusinessException(ResponseCode.BUSINESS_ERROR, '验证码发送失败，请稍后重试')

    logger.info('verification code sent: scene=%s email=%s', scene, _mask(email))
    return {'expires_in': TTL_SECONDS, 'cooldown': COOLDOWN_SECONDS}


def verify_code(request, email: str, scene: str, code: str) -> None:
    """校验并消费验证码；失败抛业务异常。"""
    email = normalize_email(email)
    code = (code or '').strip()
    if not code:
        raise BusinessException(ResponseCode.PARAM_INVALID, 'code: 请输入验证码')

    record = (VerificationCode.objects
              .filter(email=email, scene=scene, used_at__isnull=True)
              .order_by('-id').first())
    if record is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, '请先获取验证码')

    now = timezone.now()
    if record.expires_at <= now:
        record.used_at = now
        record.save(update_fields=['used_at'])
        raise BusinessException(ResponseCode.PARAM_INVALID, '验证码已过期，请重新获取')
    if record.attempts >= MAX_ATTEMPTS:
        record.used_at = now
        record.save(update_fields=['used_at'])
        raise BusinessException(ResponseCode.PARAM_INVALID, '验证码错误次数过多，请重新获取')

    if _hash_code(email, scene, code) != record.code_hash:
        record.attempts += 1
        record.save(update_fields=['attempts'])
        raise BusinessException(ResponseCode.PARAM_INVALID, '验证码错误')

    record.used_at = now
    record.save(update_fields=['used_at'])
