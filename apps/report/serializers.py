"""赛事报告输出结构。"""
from apps.common.formatting import format_dt

from .models import ReportStatus


def _match_summary(match) -> dict:
    return {
        'id': match.pk,
        'group_type': match.group_type,
        'group_type_label': match.get_group_type_display(),
        'home_team_name': match.home_team_name,
        'away_team_name': match.away_team_name,
        'home_score': match.home_score,
        'away_score': match.away_score,
        'match_time': format_dt(match.match_time),
        'venue': match.venue,
        'status': match.status,
    }


def _report_body(report) -> dict:
    return {
        'narrative': report.narrative,
        'chapters': report.chapters or [],
        'key_moments': report.key_moments or [],
        'highlights': report.highlights or [],
        'data_notes': report.data_notes or [],
        'confidence': report.confidence,
    }


def serialize_public(match, report) -> dict:
    """公众端：未生成也返回 200，用 status 区分，前端无需处理 404。"""
    body = {
        'status': report.status if report else ReportStatus.PENDING,
        'match': _match_summary(match),
        'report': None,
        'generated_at': None,
    }
    if report and report.status == ReportStatus.SUCCEEDED and report.has_content:
        body['report'] = _report_body(report)
        body['generated_at'] = format_dt(report.generated_at)
    return body


def serialize_admin(match, report) -> dict:
    """管理端：额外暴露成本、耗时与失败原因，便于运维排查。"""
    body = serialize_public(match, report)
    body['meta'] = {
        'model_name': report.model_name if report else '',
        'prompt_version': report.prompt_version if report else '',
        'input_tokens': report.input_tokens if report else None,
        'output_tokens': report.output_tokens if report else None,
        'duration_ms': report.duration_ms if report else None,
        'error_msg': report.error_msg if report else None,
        'generated_by': report.generated_by if report else None,
        'updated_at': format_dt(report.updated_at) if report else None,
    }
    return body
