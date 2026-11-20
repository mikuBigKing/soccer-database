"""遴选与评分业务逻辑。"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Avg, Count, Max, Min, Q

from apps.accounts.models import AthleteProfile
from apps.common.audit import write_audit
from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.registration.models import Competition, Registration

from .models import SelectionBestResult, SelectionProfile, SelectionScore

# 用户确认的权重要求：进球 25、防守 25、助攻 20、犯规 30
WEIGHTS = {
    'goals_score': Decimal('0.25'),
    'defense_score': Decimal('0.25'),
    'assist_score': Decimal('0.20'),
    'foul_score': Decimal('0.30'),
}


def compute_total(data: dict) -> Decimal:
    total = sum(Decimal(str(data[field])) * weight for field, weight in WEIGHTS.items())
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def get_competition(competition_id: int) -> Competition:
    competition = Competition.objects.filter(pk=competition_id).first()
    if competition is None:
        raise BusinessException(ResponseCode.PARAM_INVALID, '赛事不存在')
    return competition


@transaction.atomic
def import_profiles(request, competition_id: int, group_type: str = None, team_id: int = None) -> dict:
    """按已报名球队导入遴选档案（跳过草稿），同一球员同一赛事只建一份。"""
    get_competition(competition_id)
    registrations = (Registration.objects.filter(competition_id=competition_id)
                     .exclude(status=Registration.Status.DRAFT)
                     .select_related('team'))
    if team_id:
        registrations = registrations.filter(team_id=team_id)
    if group_type:
        registrations = registrations.filter(team__group_type=group_type)

    created, skipped = 0, 0
    for registration in registrations:
        athletes = AthleteProfile.objects.filter(
            team_id=registration.team_id, deleted_at__isnull=True,
        ).exclude(status='rejected')
        for athlete in athletes:
            exists = SelectionProfile.objects.filter(
                competition_id=competition_id, athlete=athlete
            ).exists()
            if exists:
                skipped += 1
                continue
            SelectionProfile.objects.create(
                competition_id=competition_id,
                athlete=athlete,
                team_id=athlete.team_id,
                group_type=athlete.team.group_type if athlete.team_id else '',
                name_snapshot=athlete.name,
                id_card_snapshot=athlete.id_card or '',
                created_by=request.user.id,
            )
            created += 1
    if created:
        write_audit(request, module='selection', action='导入遴选档案',
                    resource_type='competition', resource_id=competition_id,
                    after={'created': created, 'skipped': skipped})
    return {'created': created, 'skipped': skipped}


def profile_queryset(params: dict):
    qs = (SelectionProfile.objects
          .select_related('athlete', 'team', 'competition')
          .annotate(
              initial_total=Max('scores__total_score', filter=Q(scores__stage='initial')),
              final_total=Max('scores__total_score', filter=Q(scores__stage='final')),
          )
          .order_by('-id'))
    if params.get('competition_id'):
        qs = qs.filter(competition_id=params['competition_id'])
    if params.get('group_type'):
        qs = qs.filter(group_type=params['group_type'])
    if params.get('team_id'):
        qs = qs.filter(team_id=params['team_id'])
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    if params.get('id_card'):
        qs = qs.filter(id_card_snapshot__iendswith=params['id_card'].strip())
    if params.get('keyword'):
        keyword = params['keyword'].strip()
        qs = qs.filter(
            Q(name_snapshot__icontains=keyword) | Q(athlete__name__icontains=keyword)
            | Q(team__name__icontains=keyword)
        )
    return qs


def serialize_profile(profile: SelectionProfile) -> dict:
    return {
        'id': profile.id,
        'competition_id': profile.competition_id,
        'competition_name': profile.competition.name if profile.competition_id else None,
        'athlete_id': profile.athlete_id,
        'name': profile.name_snapshot or profile.athlete.name,
        'id_card': profile.id_card_snapshot or (profile.athlete.id_card or ''),
        'team_id': profile.team_id,
        'team_name': profile.team.name if profile.team_id else None,
        'group_type': profile.group_type,
        'status': profile.status,
        'status_label': profile.get_status_display(),
        'initial_total': str(profile.initial_total) if profile.initial_total is not None else None,
        'final_total': str(profile.final_total) if profile.final_total is not None else None,
        'created_at': profile.created_at.strftime('%Y-%m-%d %H:%M:%S'),
    }


@transaction.atomic
def upsert_score(request, data: dict) -> dict:
    profile = SelectionProfile.objects.filter(pk=data['profile_id']).first()
    if profile is None:
        raise BusinessException(ResponseCode.NOT_FOUND, '遴选档案不存在', status_code=404)

    score, created = SelectionScore.objects.get_or_create(
        profile=profile, stage=data['stage'],
        defaults={'judge': request.user},
    )
    for field in WEIGHTS:
        setattr(score, field, data[field])
    score.total_score = compute_total(data)
    score.judge = request.user
    score.remark = data.get('remark', '')
    score.save()

    write_audit(
        request, module='selection', action='录入评分' if created else '修改评分',
        resource_type='selection_score', resource_id=score.id,
        after={'profile_id': profile.id, 'stage': score.stage, 'total': str(score.total_score)},
    )
    return serialize_score(score)


def serialize_score(score: SelectionScore) -> dict:
    return {
        'id': score.id,
        'profile_id': score.profile_id,
        'stage': score.stage,
        'stage_label': score.get_stage_display(),
        'goals_score': score.goals_score,
        'defense_score': score.defense_score,
        'assist_score': score.assist_score,
        'foul_score': score.foul_score,
        'total_score': str(score.total_score),
        'judge_name': score.judge.name if score.judge_id else None,
        'remark': score.remark,
        'updated_at': score.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
    }


def score_queryset(params: dict):
    qs = (SelectionScore.objects
          .select_related('profile__athlete', 'profile__team', 'judge')
          .order_by('-total_score', 'profile__id'))
    if params.get('competition_id'):
        qs = qs.filter(profile__competition_id=params['competition_id'])
    if params.get('group_type'):
        qs = qs.filter(profile__group_type=params['group_type'])
    if params.get('team_id'):
        qs = qs.filter(profile__team_id=params['team_id'])
    if params.get('stage'):
        qs = qs.filter(stage=params['stage'])
    return qs


def score_row(score: SelectionScore) -> dict:
    profile = score.profile
    return {
        'id': score.id,
        'profile_id': profile.id,
        'athlete_id': profile.athlete_id,
        'name': profile.name_snapshot or profile.athlete.name,
        'team_id': profile.team_id,
        'team_name': profile.team.name if profile.team_id else None,
        'group_type': profile.group_type,
        'stage': score.stage,
        'stage_label': score.get_stage_display(),
        'goals_score': score.goals_score,
        'defense_score': score.defense_score,
        'assist_score': score.assist_score,
        'foul_score': score.foul_score,
        'total_score': float(score.total_score),
        'judge_name': score.judge.name if score.judge_id else None,
        'updated_at': score.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
    }


def summary(params: dict) -> dict:
    qs = score_queryset(params)
    by_team = list(
        qs.values('profile__team_id', 'profile__team__name')
        .annotate(count=Count('id'), avg=Avg('total_score'), best=Max('total_score'), worst=Min('total_score'))
        .order_by('-avg')
    )
    overall = qs.aggregate(count=Count('id'), avg=Avg('total_score'),
                           best=Max('total_score'), worst=Min('total_score'))
    return {
        'count': overall['count'] or 0,
        'avg': round(float(overall['avg']), 2) if overall['avg'] is not None else None,
        'best': float(overall['best']) if overall['best'] is not None else None,
        'worst': float(overall['worst']) if overall['worst'] is not None else None,
        'teams': [{
            'team_id': row['profile__team_id'],
            'team_name': row['profile__team__name'],
            'count': row['count'],
            'avg': round(float(row['avg']), 2),
            'best': float(row['best']),
            'worst': float(row['worst']),
        } for row in by_team],
    }


@transaction.atomic
def generate_best(request, competition_id: int, group_type: str, top_n: int = 11) -> dict:
    get_competition(competition_id)
    finals = (SelectionScore.objects
              .filter(profile__competition_id=competition_id,
                      profile__group_type=group_type, stage=SelectionScore.Stage.FINAL)
              .select_related('profile__athlete')
              .order_by('-total_score', '-goals_score', 'profile__id'))
    if not finals.exists():
        raise BusinessException(ResponseCode.PARAM_INVALID, '该组别暂无复选评分，无法生成结果')

    SelectionBestResult.objects.filter(
        competition_id=competition_id, group_type=group_type
    ).delete()

    results = []
    for rank, score in enumerate(finals[:top_n], start=1):
        results.append(SelectionBestResult(
            competition_id=competition_id,
            group_type=group_type,
            athlete_id=score.profile.athlete_id,
            profile=score.profile,
            rank=rank,
            total_score=score.total_score,
            published=False,
        ))
    SelectionBestResult.objects.bulk_create(results)
    write_audit(request, module='selection', action='生成最佳球员结果',
                resource_type='competition', resource_id=competition_id,
                after={'group_type': group_type, 'count': len(results)})
    return {'competition_id': competition_id, 'group_type': group_type,
            'count': len(results), 'published': False}


def best_queryset(params: dict):
    qs = (SelectionBestResult.objects
          .select_related('athlete', 'profile__team')
          .order_by('group_type', 'rank'))
    if params.get('competition_id'):
        qs = qs.filter(competition_id=params['competition_id'])
    if params.get('group_type'):
        qs = qs.filter(group_type=params['group_type'])
    if params.get('published') is not None:
        qs = qs.filter(published=params['published'])
    return qs


def best_row(result: SelectionBestResult) -> dict:
    return {
        'id': result.id,
        'competition_id': result.competition_id,
        'group_type': result.group_type,
        'rank': result.rank,
        'athlete_id': result.athlete_id,
        'name': result.athlete.name,
        'team_id': result.profile.team_id if result.profile_id else None,
        'team_name': result.profile.team.name if result.profile_id and result.profile.team_id else None,
        'total_score': float(result.total_score),
        'published': result.published,
        'generated_at': result.generated_at.strftime('%Y-%m-%d %H:%M:%S'),
    }


@transaction.atomic
def publish_best(request, competition_id: int, group_type: str, published: bool = True) -> dict:
    count = SelectionBestResult.objects.filter(
        competition_id=competition_id, group_type=group_type
    ).update(published=published)
    if count == 0:
        raise BusinessException(ResponseCode.NOT_FOUND, '暂无可发布的结果', status_code=404)
    write_audit(request, module='selection',
                action='发布最佳球员结果' if published else '取消发布最佳球员结果',
                resource_type='competition', resource_id=competition_id,
                after={'group_type': group_type, 'count': count})
    return {'competition_id': competition_id, 'group_type': group_type,
            'published': published, 'count': count}
