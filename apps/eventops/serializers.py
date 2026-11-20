"""赛事运营后台序列化器。

沿用目标项目风格：
- 唯一性交给数据库约束 + 视图层 IntegrityError → BusinessException(DUPLICATE_SUBMIT)
- 文件四重校验（扩展名 + MIME + 文件头 + 大小，20MB）
- 业务异常抛 BusinessException；参数校验用 ValidationError
"""
import hashlib
import os

from rest_framework import serializers

from apps.accounts.models import AthleteProfile
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.live.models import Match
from apps.org.models import Team

from .models import (
    EventRevision,
    MatchDocument,
    MatchDocumentVersion,
    MatchEvent,
    MatchOfficial,
    MatchRecord,
    OperationLog,
    Referee,
    DocType,
    EventPeriod,
    EventType,
)


# ---------------------------------------------------------------------------
# 文件四重校验
# ---------------------------------------------------------------------------

DOC_TYPE_ALLOWS = {
    DocType.SIGN_IN: {
        'allowed_exts': {'pdf', 'jpg', 'jpeg', 'png', 'xlsx'},
        'allowed_mimes': {
            'application/pdf', 'image/jpeg', 'image/png', 'image/jpg',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        },
        'max_size_mb': 20,
    },
    DocType.RECORD_CONFIRM: {
        'allowed_exts': {'pdf', 'jpg', 'jpeg', 'png'},
        'allowed_mimes': {'application/pdf', 'image/jpeg', 'image/png', 'image/jpg'},
        'max_size_mb': 20,
    },
    DocType.REFEREE_REPORT: {
        'allowed_exts': {'pdf', 'docx'},
        'allowed_mimes': {
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword',
        },
        'max_size_mb': 20,
    },
}

DOC_TYPE_PART_KEYS = {
    DocType.SIGN_IN: {'main', 'home', 'away'},
    DocType.RECORD_CONFIRM: {'main'},
    DocType.REFEREE_REPORT: {'main'},
}

IMAGE_MAGIC = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'%PDF': 'application/pdf',
}


def _detect_mime(file_bytes: bytes) -> str:
    for magic, mime in IMAGE_MAGIC.items():
        if file_bytes.startswith(magic):
            return mime
    return ''


def validate_uploaded_file(file_obj, doc_type: str):
    """文件四重校验。返回 (sha256_hex, mime_detected, size, ext)，或抛 BusinessException."""
    rules = DOC_TYPE_ALLOWS.get(doc_type)
    if not rules:
        raise BusinessException(ResponseCode.PARAM_INVALID, '未知文件类型')

    max_size = rules['max_size_mb'] * 1024 * 1024

    # 1. 大小
    size = getattr(file_obj, 'size', None)
    if size is None:
        size = len(file_obj.read())
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
    if size > max_size:
        raise BusinessException(ResponseCode.FILE_SIZE_EXCEEDED, f'文件超出 {rules["max_size_mb"]}MB')

    # 2. 扩展名
    original = file_obj.name or ''
    ext = os.path.splitext(original)[1].lstrip('.').lower() or 'bin'
    if ext not in rules['allowed_exts']:
        raise BusinessException(
            ResponseCode.FILE_TYPE_INVALID,
            f'{doc_type} 仅允许格式: {", ".join(sorted(rules["allowed_exts"]))}',
        )

    # 3. 文件头检测 MIME（仅 PDF / JPEG / PNG；xlsx/docx 是 zip 容器跳过魔法校验）
    file_bytes = file_obj.read()
    detected = _detect_mime(file_bytes[:16])
    is_zip = file_bytes.startswith(b'PK\x03\x04')
    if detected:
        if detected == 'application/pdf' and ext != 'pdf':
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID, '文件内容与扩展名不符（PDF 头不是 pdf 扩展名）')
        if detected == 'image/jpeg' and ext not in {'jpg', 'jpeg'}:
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID, 'JPEG 内容但扩展名不是 jpg/jpeg')
        if detected == 'image/png' and ext != 'png':
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID, 'PNG 内容但扩展名不是 png')

    # 4. MIME 白名单
    declared_mime = (file_obj.content_type or '').lower()
    if declared_mime and declared_mime not in rules['allowed_mimes']:
        if detected and detected not in rules['allowed_mimes']:
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID, f'文件 MIME {declared_mime} 不在允许范围')

    sha256 = hashlib.sha256(file_bytes).hexdigest()
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    return sha256, detected or declared_mime or 'application/octet-stream', size, ext


def validate_part_key(doc_type: str, part_key: str):
    allowed = DOC_TYPE_PART_KEYS.get(doc_type)
    if allowed and part_key not in allowed:
        raise BusinessException(
            ResponseCode.PARAM_INVALID, f'{doc_type} 不允许分册 {part_key}，仅允许 {sorted(allowed)}',
        )


# ---------------------------------------------------------------------------
# 裁判员
# ---------------------------------------------------------------------------

class RefereeSerializer(serializers.ModelSerializer):
    level_display = serializers.CharField(source='get_level_display', read_only=True)
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)

    class Meta:
        model = Referee
        fields = [
            'id', 'name', 'gender', 'gender_display', 'level', 'level_display',
            'certificate_no', 'phone', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        # 关闭 DRF UniqueValidator，统一由 IntegrityError → DUPLICATE_SUBMIT 处理
        extra_kwargs = {'certificate_no': {'validators': []}}


# ---------------------------------------------------------------------------
# 比赛裁判安排
# ---------------------------------------------------------------------------

class MatchOfficialSerializer(serializers.ModelSerializer):
    referee_name = serializers.CharField(source='referee.name', read_only=True)
    referee_certificate = serializers.CharField(source='referee.certificate_no', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = MatchOfficial
        fields = [
            'id', 'match', 'referee', 'referee_name', 'referee_certificate',
            'role', 'role_display', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        validators = []  # 关闭 UniqueTogetherValidator，交给数据库约束
        extra_kwargs = {
            # match 由 URL /matches/{match_id}/officials/ 注入
            'match': {'required': False},
        }


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------

class MatchEventSerializer(serializers.ModelSerializer):
    match_label = serializers.CharField(source='match.__str__', read_only=True)
    team_name = serializers.CharField(source='team.name', read_only=True)
    athlete_name = serializers.CharField(source='athlete.name', read_only=True)
    related_athlete_name = serializers.CharField(source='related_athlete.name', read_only=True)
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    period_display = serializers.CharField(source='get_period_display', read_only=True)

    class Meta:
        model = MatchEvent
        fields = [
            'id', 'match', 'match_label',
            'event_type', 'event_type_display',
            'period', 'period_display', 'minute', 'stoppage_minute',
            'team', 'team_name', 'athlete', 'athlete_name',
            'related_athlete', 'related_athlete_name',
            'description', 'version', 'recorded_source',
            'is_deleted', 'deleted_at', 'delete_reason',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'version', 'recorded_source', 'is_deleted',
            'deleted_at', 'deleted_by', 'delete_reason',
            'created_at', 'updated_at',
        ]


class MatchEventWriteSerializer(serializers.ModelSerializer):
    """新增 / 修改事件。match 必填（由 URL /matches/{match_id}/events/ 注入）。"""

    match = serializers.PrimaryKeyRelatedField(queryset=Match.objects.all(), required=False)
    change_reason = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = MatchEvent
        fields = [
            'id', 'match', 'event_type', 'period', 'minute', 'stoppage_minute',
            'team', 'athlete', 'related_athlete', 'description',
            'version', 'change_reason',
        ]
        extra_kwargs = {
            'match': {'required': False},        # 允许视图从 URL 注入
            'version': {'required': False},      # 乐观锁字段，修改时必须带
        }

    def validate(self, data):
        # match 可以来自 self.instance 或 URL view.kwargs
        match = data.get('match') or getattr(self.instance, 'match', None)
        if match is None:
            view = self.context.get('view')
            match_id = getattr(view, 'kwargs', {}).get('match_id')
            if match_id:
                match = Match.objects.filter(pk=match_id).first()
        if match is None:
            raise BusinessException(ResponseCode.PARAM_INVALID, 'match 必填')

        team = data.get('team')
        if team is None and self.instance:
            team = self.instance.team
        if team is not None and team.id not in (match.home_team_id, match.away_team_id):
            raise BusinessException(ResponseCode.PARAM_INVALID, '事件球队必须是本场主客队之一')

        athlete = data.get('athlete')
        if athlete is not None:
            # 球员必须属于该球队（AthleteProfile.team 外键）
            if team is not None and athlete.team_id != team.id:
                raise BusinessException(ResponseCode.PARAM_INVALID, '球员不属于所选球队')

        minute = data.get('minute', getattr(self.instance, 'minute', None))
        if minute is not None and not (1 <= minute <= 120):
            raise BusinessException(ResponseCode.PARAM_INVALID, 'minute 必须在 1-120 之间')
        stoppage = data.get('stoppage_minute', getattr(self.instance, 'stoppage_minute', 0))
        if stoppage is not None and not (0 <= stoppage <= 10):
            raise BusinessException(ResponseCode.PARAM_INVALID, 'stoppage_minute 必须在 0-10 之间')

        return data


# ---------------------------------------------------------------------------
# 快照 / 版本
# ---------------------------------------------------------------------------

class EventRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventRevision
        fields = '__all__'


# ---------------------------------------------------------------------------
# 文件档案
# ---------------------------------------------------------------------------

class _CurrentVersionField(serializers.Field):
    """输出 MatchDocument.current_version 详情（只读）。"""

    def to_representation(self, value):
        cur_no = value.current_version_no
        try:
            v = value.versions.get(version_no=cur_no)
        except MatchDocumentVersion.DoesNotExist:
            return None
        return MatchDocumentVersionSerializer(v).data


class MatchDocumentSerializer(serializers.ModelSerializer):
    doc_type_display = serializers.CharField(source='get_doc_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    current_version = _CurrentVersionField(read_only=True)

    class Meta:
        model = MatchDocument
        fields = [
            'id', 'match', 'doc_type', 'doc_type_display', 'part_key', 'title',
            'status', 'status_display', 'current_version_no', 'current_version',
            'archived_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'current_version_no', 'archived_at', 'created_at', 'updated_at']


class MatchDocumentCreateSerializer(serializers.ModelSerializer):
    """新建档案 + 首个版本（multipart/form-data）。"""

    file = serializers.FileField(required=True, write_only=True)
    change_note = serializers.CharField(required=False, default='', write_only=True)
    match = serializers.PrimaryKeyRelatedField(queryset=Match.objects.all(), required=False)

    class Meta:
        model = MatchDocument
        fields = ['id', 'match', 'doc_type', 'part_key', 'title', 'file', 'change_note']
        read_only_fields = ['id']
        validators = []  # unique_together 交给数据库


class MatchDocumentUpdateSerializer(serializers.ModelSerializer):
    """仅允许修改 title（归档后仍可用，但视图层会拒绝）。"""

    class Meta:
        model = MatchDocument
        fields = ['title']


class MatchDocumentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchDocumentVersion
        fields = [
            'id', 'document', 'version_no', 'file_path', 'original_name',
            'file_ext', 'mime_type', 'file_size', 'sha256',
            'change_note', 'uploaded_by', 'created_at',
        ]


class DocumentVersionUploadSerializer(serializers.Serializer):
    """上传新版本（不绑到具体模型，file 直接在 view 层落盘）。"""

    file = serializers.FileField(required=True)
    change_note = serializers.CharField(required=False, default='')


# ---------------------------------------------------------------------------
# 归档 / 操作日志
# ---------------------------------------------------------------------------

class MatchRecordSerializer(serializers.ModelSerializer):
    match_label = serializers.CharField(source='match.__str__', read_only=True)

    class Meta:
        model = MatchRecord
        fields = '__all__'


class FinalizeRequestSerializer(serializers.Serializer):
    lock_note = serializers.CharField(required=False, default='', allow_blank=True)


class UnfinalizeRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)


class OperationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationLog
        fields = '__all__'
