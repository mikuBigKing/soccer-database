"""统一响应码（与 Laravel app/Enums/ResponseCode.php 一一对应）。

码段：
- 0        成功
- 100xx    参数 / 校验
- 200xx    认证 / 权限
- 300xx    资源不存在
- 400xx    业务规则
- 500xx    第三方 / 外部服务
- 600xx    数据存储
- 900xx    系统异常
"""
from enum import IntEnum


class ResponseCode(IntEnum):
    SUCCESS = 0

    # 100xx 参数校验
    PARAM_INVALID = 10001
    FILE_TYPE_INVALID = 10006
    FILE_SIZE_EXCEEDED = 10007

    # 200xx 认证 / 权限
    UNAUTHENTICATED = 20001
    CREDENTIALS_INVALID = 20002
    ACCOUNT_DISABLED = 20003
    FORBIDDEN = 20004

    # 300xx 资源
    NOT_FOUND = 30001

    # 400xx 业务规则
    OPTIMISTIC_LOCK_CONFLICT = 9
    BUSINESS_ERROR = 40001
    MATCH_FINISHED_LOCKED = 40002
    DUPLICATE_SUBMIT = 40009

    # 500xx 外部服务
    FACE_VERIFY_FAILED = 50008

    # 600xx 存储
    DB_ERROR = 60001

    # 900xx 系统
    SYSTEM_ERROR = 90001


MESSAGES = {
    ResponseCode.SUCCESS: 'success',
    ResponseCode.PARAM_INVALID: '参数校验失败',
    ResponseCode.FILE_TYPE_INVALID: '不支持的文件格式',
    ResponseCode.FILE_SIZE_EXCEEDED: '文件大小超出限制',
    ResponseCode.UNAUTHENTICATED: '未登录或登录已过期',
    ResponseCode.CREDENTIALS_INVALID: '账号或密码错误',
    ResponseCode.ACCOUNT_DISABLED: '账号已被禁用',
    ResponseCode.FORBIDDEN: '无操作权限',
    ResponseCode.NOT_FOUND: '资源不存在',
    ResponseCode.OPTIMISTIC_LOCK_CONFLICT: '数据已被他人修改，请刷新后重试',
    ResponseCode.BUSINESS_ERROR: '业务处理失败',
    ResponseCode.MATCH_FINISHED_LOCKED: '比赛已结束，禁止修改',
    ResponseCode.DUPLICATE_SUBMIT: '请勿重复提交',
    ResponseCode.FACE_VERIFY_FAILED: '人脸核验服务异常',
    ResponseCode.DB_ERROR: '数据存储异常',
    ResponseCode.SYSTEM_ERROR: '系统繁忙，请稍后重试',
}


def message_of(code: int) -> str:
    try:
        return MESSAGES[ResponseCode(code)]
    except (ValueError, KeyError):
        return '请求失败'
