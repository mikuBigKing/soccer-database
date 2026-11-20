"""邮箱占位与账号标识辅助。"""
import uuid


def placeholder_email(phone: str = None) -> str:
    """内部创建账号时没有真实邮箱，生成不可投递占位邮箱，后续由管理员补录。"""
    if phone:
        return f'{phone}@placeholder.local'
    return f'user-{uuid.uuid4().hex[:10]}@placeholder.local'


def normalize_email(email: str) -> str:
    return (email or '').strip().lower()
