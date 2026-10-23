"""赛事直播业务逻辑（对应 Laravel MatchService / MatchEventService / RefereeService /
MatchFileService / FaceVerifyService）。

约定：
- 事务在 service 层，事务内不做外部 IO（文件落盘放在事务外或之后）
- 乐观锁：update 类操作以 version 为条件，影响行数为 0 抛 code=9
- 事件：client_token 幂等（40009）；goal 事件在锁内联动比分
"""
import logging
import os
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException

from .models import (
    EventType,
    FaceVerifyRecord,
    Match,
    MatchEvent,
    MatchFile,
    MatchReferee,
    MatchStatus,
    RefereeRole,
)

business_logger = logging.getLogger('business')


class MatchService:
    def create(self, data: dict, user_id: int) -> Match:
        match = Match.objects.create(
            **data,
            status=MatchStatus.NOT_STARTED,
            created_by=user_id,
            updated_by=user_id,
        )
        business_logger.info('match created match_id=%s operator=%s', match.id, user_id)
        return match

    def update(self, match: Match, data: dict, version: int, user_id: int) -> Match:
        self._ensure_editable(match)

        payload = {k: v for k, v in data.items() if k != 'version'}
        payload.update({'updated_by': user_id, 'version': version + 1})
        with transaction.atomic():
            affected = Match.objects.filter(pk=match.pk, version=version).update(**payload)
            if affected == 0:
                raise BusinessException(ResponseCode.OPTIMISTIC_LOCK_CONFLICT)
        business_logger.info('match updated match_id=%s operator=%s fields=%s', match.pk, user_id, list(data))
        return Match.objects.get(pk=match.pk)

    def delete(self, match: Match, user_id: int) -> None:
        if match.status != MatchStatus.NOT_STARTED:
            raise BusinessException(ResponseCode.MATCH_FINISHED_LOCKED, '仅未开始的比赛可删除')
        with transaction.atomic():
            Match.objects.filter(pk=match.pk).update(deleted_at=timezone.now())
        business_logger.info('match deleted match_id=%s operator=%s', match.pk, user_id)

    def change_status(self, match: Match, action: str, version: int, user_id: int) -> Match:
        expected = {
            'start': (MatchStatus.NOT_STARTED, MatchStatus.LIVE),
            'finish': (MatchStatus.LIVE, MatchStatus.FINISHED),
        }
        from_status, to_status = expected[action]

        if match.status != from_status:
            raise BusinessException(
                ResponseCode.MATCH_FINISHED_LOCKED,
                f'当前状态[{match.get_status_display()}]不允许执行[{action}]',
            )

        with transaction.atomic():
            affected = Match.objects.filter(pk=match.pk, version=version).update(
                status=to_status, version=version + 1, updated_by=user_id
            )
            if affected == 0:
                raise BusinessException(ResponseCode.OPTIMISTIC_LOCK_CONFLICT)
        business_logger.info(
            'match status changed match_id=%s operator=%s before=%s after=%s',
            match.pk, user_id, from_status, to_status,
        )
        return Match.objects.get(pk=match.pk)

    def update_lineup(self, match: Match, lineup_home: list, lineup_away: list, version: int, user_id: int) -> Match:
        self._ensure_editable(match)
        with transaction.atomic():
            affected = Match.objects.filter(pk=match.pk, version=version).update(
                lineup_home=lineup_home,
                lineup_away=lineup_away,
                version=version + 1,
                updated_by=user_id,
            )
            if affected == 0:
                raise BusinessException(ResponseCode.OPTIMISTIC_LOCK_CONFLICT)
        business_logger.info(
            'match lineup updated match_id=%s operator=%s home=%s away=%s',
            match.pk, user_id, len(lineup_home), len(lineup_away),
        )
        return Match.objects.get(pk=match.pk)

    @staticmethod
    def _ensure_editable(match: Match) -> None:
        if match.status == MatchStatus.FINISHED:
            raise BusinessException(ResponseCode.MATCH_FINISHED_LOCKED)


class MatchEventService:
    def add_event(self, match_id: int, data: dict, user_id: int) -> MatchEvent:
        with transaction.atomic():
            match = Match.objects.select_for_update().get(pk=match_id)
            self._ensure_not_finished(match)

            if MatchEvent.objects.filter(client_token=data['client_token']).exists():
                raise BusinessException(ResponseCode.DUPLICATE_SUBMIT)

            event = MatchEvent.objects.create(match=match, created_by=user_id, **data)
            self._apply_score(match, event.type, event.team_side, 1)

        business_logger.info(
            'match event added event_id=%s match_id=%s operator=%s type=%s minute=%s side=%s',
            event.id, match_id, user_id, event.type, event.minute, event.team_side,
        )
        return event

    def update_event(self, event: MatchEvent, data: dict, user_id: int) -> MatchEvent:
        with transaction.atomic():
            match = Match.objects.select_for_update().get(pk=event.match_id)
            self._ensure_not_finished(match)

            # 先回退旧记分，更新后按新值重算（归属可能变化）
            self._apply_score(match, event.type, event.team_side, -1)
            for key, value in data.items():
                setattr(event, key, value)
            event.save()
            self._apply_score(match, event.type, event.team_side, 1)

        business_logger.info(
            'match event updated event_id=%s match_id=%s operator=%s fields=%s',
            event.id, event.match_id, user_id, list(data),
        )
        return event

    def delete_event(self, event: MatchEvent, user_id: int) -> None:
        with transaction.atomic():
            match = Match.objects.select_for_update().get(pk=event.match_id)
            self._ensure_not_finished(match)
            self._apply_score(match, event.type, event.team_side, -1)
            event_id = event.id
            event.delete()
        business_logger.info('match event deleted event_id=%s match_id=%s operator=%s', event_id, event.match_id, user_id)

    @staticmethod
    def _apply_score(match: Match, event_type: str, team_side, delta: int) -> None:
        """goal 事件按 team_side 在锁内联动比分，同时 version+1。"""
        if event_type != EventType.GOAL or team_side not in ('home', 'away'):
            return
        column = 'home_score' if team_side == 'home' else 'away_score'
        current = getattr(match, column)
        setattr(match, column, max(0, current + delta))
        match.version = match.version + 1
        match.save(update_fields=[column, 'version', 'updated_at'])

    @staticmethod
    def _ensure_not_finished(match: Match) -> None:
        if match.status == MatchStatus.FINISHED:
            raise BusinessException(ResponseCode.MATCH_FINISHED_LOCKED)


class RefereeService:
    """裁判整体替换：chief/fourth ≤ 1，assistant ≤ 2，总数 ≤ 4。"""

    LIMITS = {RefereeRole.CHIEF: 1, RefereeRole.FOURTH: 1, RefereeRole.ASSISTANT: 2}

    def list_for_match(self, match: Match):
        return match.referees.all().order_by('id')

    def replace(self, match: Match, referees: list, user_id: int):
        counts = {}
        for item in referees:
            counts[item['role']] = counts.get(item['role'], 0) + 1

        for role, limit in self.LIMITS.items():
            if counts.get(role, 0) > limit:
                raise BusinessException(
                    ResponseCode.PARAM_INVALID,
                    f'{RefereeRole(role).label}最多 {limit} 人',
                )

        with transaction.atomic():
            MatchReferee.objects.filter(match=match).delete()
            MatchReferee.objects.bulk_create(
                [MatchReferee(match=match, role=i['role'], name=i['name']) for i in referees]
            )
        business_logger.info('match referees replaced match_id=%s operator=%s count=%s', match.pk, user_id, len(referees))
        return match.referees.all().order_by('id')


class MatchFileService:
    """赛事文件：pdf/jpg/jpeg/png ≤ 10MB，落私有目录，下载需鉴权。"""

    ALLOWED_EXT = {'.pdf', '.jpg', '.jpeg', '.png'}

    def list_for_match(self, match: Match):
        return match.files.all().order_by('id')

    def upload(self, match: Match, file_type: str, uploaded_file, user_id: int) -> MatchFile:
        name = uploaded_file.name or 'file'
        ext = os.path.splitext(name)[1].lower()
        if ext not in self.ALLOWED_EXT:
            raise BusinessException(ResponseCode.FILE_TYPE_INVALID)
        if uploaded_file.size > settings.MAX_FILE_SIZE:
            raise BusinessException(ResponseCode.FILE_SIZE_EXCEEDED)

        relative_dir = os.path.join('matches', str(match.pk))
        absolute_dir = os.path.join(settings.PRIVATE_STORAGE_ROOT, relative_dir)
        os.makedirs(absolute_dir, exist_ok=True)

        stored_name = f'{uuid.uuid4().hex}{ext}'
        relative_path = os.path.join(relative_dir, stored_name)
        absolute_path = os.path.join(settings.PRIVATE_STORAGE_ROOT, relative_path)
        with open(absolute_path, 'wb') as target:
            for chunk in uploaded_file.chunks():
                target.write(chunk)

        record = MatchFile.objects.create(
            match=match,
            type=file_type,
            file_name=name,
            file_path=relative_path,
            mime=uploaded_file.content_type or 'application/octet-stream',
            size=uploaded_file.size,
            uploaded_by=user_id,
        )
        business_logger.info(
            'match file uploaded file_id=%s match_id=%s operator=%s type=%s size=%s',
            record.id, match.pk, user_id, file_type, record.size,
        )
        return record

    def absolute_path(self, record: MatchFile) -> str:
        return os.path.join(settings.PRIVATE_STORAGE_ROOT, record.file_path)

    def delete(self, record: MatchFile, user_id: int) -> None:
        absolute_path = self.absolute_path(record)
        record.delete()
        if os.path.exists(absolute_path):
            os.remove(absolute_path)
        business_logger.info('match file deleted file_id=%s operator=%s', record.pk, user_id)


class FaceVerifyService:
    """人脸核验（mock driver）；外部异常返回 50008。"""

    def records(self, match: Match):
        return match.face_verify_records.all().order_by('-id')

    def verify(self, match: Match, player_name: str, player_no, photo, user_id: int) -> FaceVerifyRecord:
        relative_dir = os.path.join('face_verify', str(match.pk))
        absolute_dir = os.path.join(settings.PRIVATE_STORAGE_ROOT, relative_dir)
        os.makedirs(absolute_dir, exist_ok=True)
        stored_name = f'{uuid.uuid4().hex}{os.path.splitext(photo.name or "photo.jpg")[1].lower() or ".jpg"}'
        relative_path = os.path.join(relative_dir, stored_name)
        with open(os.path.join(settings.PRIVATE_STORAGE_ROOT, relative_path), 'wb') as target:
            for chunk in photo.chunks():
                target.write(chunk)

        # mock 驱动：固定返回通过，真实驱动接入时替换此处
        try:
            result = FaceVerifyRecord.Result.PASS
            score = 95.00
            channel = 'mock'
            request_id = uuid.uuid4().hex
        except Exception as exc:  # pragma: no cover
            raise BusinessException(ResponseCode.FACE_VERIFY_FAILED) from exc

        record = FaceVerifyRecord.objects.create(
            match=match,
            player_name=player_name,
            player_no=player_no,
            photo_path=relative_path,
            result=result,
            score=score,
            channel=channel,
            request_id=request_id,
            created_by=user_id,
        )
        business_logger.info(
            'face verify done record_id=%s match_id=%s operator=%s result=%s',
            record.id, match.pk, user_id, result,
        )
        return record
