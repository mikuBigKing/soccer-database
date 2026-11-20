"""项目配置。

对应 Laravel 侧 .env 口径：
- DB_ENGINE=sqlite|mysql，默认 sqlite 便于本地冒烟
- CACHE/SESSION 不依赖外部服务
"""
import importlib.util
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / '.env')
except ImportError:  # pragma: no cover
    pass

# 开发期默认密钥只允许在 DEBUG 或本地管理命令（test/check/migrate 等）下使用，
# 生产环境缺 DJANGO_SECRET_KEY 直接启动失败，避免误用公开密钥。
DEV_SECRET_KEY = 'django-insecure-dev-only-key-please-change'
_LOCAL_COMMANDS = {
    'test', 'check', 'makemigrations', 'migrate', 'showmigrations', 'sqlmigrate',
    'shell', 'collectstatic', 'createsuperuser', 'seed_admin', 'smoke_test.py',
}


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, '1' if default else '0').strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name: str, default: str = '') -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


DEBUG = env_bool('DJANGO_DEBUG', False)
_RUNNING_LOCAL_COMMAND = bool({Path(arg).name for arg in sys.argv[:2]} & _LOCAL_COMMANDS)

# ---------------------------------------------------------------------------
# 邮件 / 邮箱验证码
# 开发默认 console 后端（验证码打印到控制台），生产在 .env 配置 SMTP 即可
# ---------------------------------------------------------------------------
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '465'))
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', True)
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '10'))
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', '赛事系统 <no-reply@localhost>')

VERIFICATION_CODE_TTL_SECONDS = int(os.getenv('VERIFICATION_CODE_TTL_SECONDS', '600'))
VERIFICATION_CODE_COOLDOWN_SECONDS = int(os.getenv('VERIFICATION_CODE_COOLDOWN_SECONDS', '60'))
VERIFICATION_CODE_HOURLY_LIMIT = int(os.getenv('VERIFICATION_CODE_HOURLY_LIMIT', '5'))
VERIFICATION_CODE_DAILY_LIMIT = int(os.getenv('VERIFICATION_CODE_DAILY_LIMIT', '10'))
VERIFICATION_CODE_MAX_ATTEMPTS = int(os.getenv('VERIFICATION_CODE_MAX_ATTEMPTS', '5'))

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG or _RUNNING_LOCAL_COMMAND:
        SECRET_KEY = DEV_SECRET_KEY
    else:
        raise ImproperlyConfigured(
            '生产环境必须通过环境变量 DJANGO_SECRET_KEY 配置密钥（本地开发请在 .env 设置 DJANGO_DEBUG=1）'
        )

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
if not DEBUG and ('*' in ALLOWED_HOSTS or not ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        '生产环境必须通过 DJANGO_ALLOWED_HOSTS 显式配置允许的域名，且不能使用 *'
    )

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # 第三方
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    # 业务
    'apps.accounts',
    'apps.org',
    'apps.live',
    'apps.athlete',
    'apps.registration',
    'apps.admin_console',
    'apps.selection',
    'apps.report',
    'apps.eventops',
]

# 前后端分域部署时启用 CORS（同源反向代理部署可不配置）。
# 依赖未安装时不影响同源部署启动。
HAS_CORS = importlib.util.find_spec('corsheaders') is not None
if HAS_CORS:
    INSTALLED_APPS.insert(1, 'corsheaders')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
]
if HAS_CORS:
    MIDDLEWARE.append('corsheaders.middleware.CorsMiddleware')
MIDDLEWARE += [
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # trace_id + 访问日志（对应 Laravel 的 TraceIdMiddleware / ApiLogMiddleware）
    'apps.common.middleware.TraceIDMiddleware',
    'apps.common.middleware.ApiLogMiddleware',
    'apps.common.audit.AuditLogMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

if os.getenv('DB_ENGINE', 'sqlite').lower() == 'mysql':
    # 生产 MySQL：优先使用 PyMySQL（纯 Python，免编译），未安装时回退 mysqlclient
    try:  # pragma: no cover
        import pymysql

        pymysql.install_as_MySQLdb()
    except ImportError:  # pragma: no cover
        pass
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.getenv('DB_NAME', 'soccer'),
            'USER': os.getenv('DB_USER', 'root'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('DB_PORT', '3306'),
            'OPTIONS': {'charset': 'utf8mb4', 'init_command': "SET sql_mode='STRICT_TRANS_TABLES'"},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / os.getenv('DB_NAME', 'db.sqlite3'),
        }
    }

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 6}},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# 赛事文件（培训签到表/记录确认单/裁判报告单）走私有目录，下载需鉴权
PRIVATE_STORAGE_ROOT = BASE_DIR / 'storage' / 'private'

MAX_IMAGE_SIZE = int(os.getenv('MAX_IMAGE_SIZE', str(10 * 1024 * 1024)))
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', str(10 * 1024 * 1024)))

# 管理员重置球员/教练登录密码后的默认密码（任何日志/响应都不落明文）
DEFAULT_RESET_PASSWORD = os.getenv('DEFAULT_RESET_PASSWORD', 'Abc@123456')

# 管理员自定义重置密码时的密码策略（字母 + 数字，8–64 位）
PASSWORD_POLICY = os.getenv('PASSWORD_POLICY', r'^(?=.*[A-Za-z])(?=.*\d).{8,64}$')

# 停赛规则（红黄牌累计自动停赛）
SUSPENSION_RULES = {
    'yellow_threshold': 4,        # 累计 4 张黄牌
    'yellow_ban_matches': 1,      # 停赛 1 场
    'red_ban_matches': 2,         # 红牌停 2 场
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'apps.accounts.authentication.ActiveUserJWTAuthentication',
    ),
    # 默认要求登录，公开接口在视图上显式 AllowAny，避免新视图默认裸奔。
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'apps.common.renderers.UnifiedJSONRenderer',
    ),
    'EXCEPTION_HANDLER': 'apps.common.exceptions.custom_exception_handler',
    'UNAUTHENTICATED_USER': 'django.contrib.auth.models.AnonymousUser',
    'DEFAULT_FILTER_BACKENDS': ('django_filters.rest_framework.DjangoFilterBackend',),
}

if HAS_CORS:
    # 分域部署白名单：默认只放开本地开发端口，生产通过环境变量覆盖。
    CORS_ALLOWED_ORIGINS = env_list(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost:8083,http://127.0.0.1:8083',
    )
    CORS_ALLOW_CREDENTIALS = True
    CORS_ALLOW_HEADERS = (
        'accept', 'accept-encoding', 'authorization', 'content-type',
        'dnt', 'origin', 'user-agent', 'x-csrftoken', 'x-requested-with',
        'x-request-id',
    )
    CORS_EXPOSE_HEADERS = ('X-Request-ID',)

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
    'UPDATE_LAST_LOGIN': True,
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '[%(asctime)s] %(levelname)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        # 业务日志通道（对应 Laravel business channel）
        'business': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        # 访问日志通道（对应 Laravel api channel）
        'api': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

# ---------------------------------------------------------------------------
# 赛事报告 AI（Qwen3 / OpenAI 兼容端点）
# ---------------------------------------------------------------------------
REPORT_ENABLED = os.getenv('REPORT_ENABLED', '1') == '1'
REPORT_LLM_BASE_URL = os.getenv('REPORT_LLM_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
REPORT_LLM_API_KEY = os.getenv('REPORT_LLM_API_KEY', '')
REPORT_LLM_MODEL = os.getenv('REPORT_LLM_MODEL', 'qwen3-max')
REPORT_LLM_TIMEOUT = int(os.getenv('REPORT_LLM_TIMEOUT', '60'))
REPORT_LLM_TEMPERATURE = float(os.getenv('REPORT_LLM_TEMPERATURE', '0.3'))
REPORT_LLM_MAX_TOKENS = int(os.getenv('REPORT_LLM_MAX_TOKENS', '2000'))
# Qwen3 思考模式会产生大量 reasoning token（实测同一场报告 338s / 3688 tokens），生产默认关闭
REPORT_LLM_DISABLE_THINKING = os.getenv('REPORT_LLM_DISABLE_THINKING', '1') == '1'
# 未配置 API Key 时自动走 mock，保证本地与 CI 可用
REPORT_LLM_MOCK = os.getenv('REPORT_LLM_MOCK', '1' if not REPORT_LLM_API_KEY else '0') == '1'
