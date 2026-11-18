"""运动员端业务逻辑（对应 Laravel AthleteService）。"""
from django.db.models import Count

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.accounts.models import AthleteProfile, ProfileStatus
from apps.accounts.validators import ensure_id_card_unique
from apps.live.models import EventType, MatchEvent, MatchStatus
from apps.live.models import Match as MatchInfo
from apps.org.models import Team, TeamStatus
from apps.registration.models import Competition, Registration


class AthleteService:
    def profile(self, user) -> dict:
        profile = self._require_profile(user)
        team = profile.team
        return {
            'id': profile.id,
            'name': profile.name,
            'jersey_no': profile.jersey_no,
            'position': profile.position,
            'status': profile.status,
            'reject_reason': profile.reject_reason,
            'team': {
                'id': team.id,
                'name': team.name,
                'logo': team.logo,
                'group_type': team.group_type,
                'status': team.status,
            } if team else None,
        }

    def update_profile(self, user, data: dict) -> dict:
        profile = self._require_profile(user)
        if 'jersey_no' in data and data['jersey_no'] is not None and profile.team_id:
            duplicated = AthleteProfile.objects.filter(
                team_id=profile.team_id, jersey_no=data['jersey_no']
            ).exclude(pk=profile.pk).exists()
            if duplicated:
                raise BusinessException(ResponseCode.PARAM_INVALID, '该球衣号在本队已被占用')
        if data.get('name'):
            profile.name = data['name']
            user.name = data['name']
            user.save(update_fields=['name'])
        if 'id_card' in data:
            new_card = ensure_id_card_unique(data['id_card'], athlete_pk=profile.pk)
            if new_card != profile.id_card:
                profile.id_card = new_card
                # 身份证变更后需校级重新实名审核
                if profile.status == ProfileStatus.APPROVED:
                    profile.status = ProfileStatus.PENDING
                    profile.reject_reason = None
        if 'jersey_no' in data:
            profile.jersey_no = data['jersey_no']
        if 'position' in data:
            profile.position = data['position']
        profile.save()
        return self.profile(user)

    def matches(self, user, status_value: str = None, competition_id: int = None) -> dict:
        profile = self._require_profile(user)
        team_id = profile.team_id
        if not team_id:
            return {'list': [], 'team_id': None}

        from django.db.models import Q

        queryset = MatchInfo.objects.select_related('competition').filter(
            Q(home_team=team_id) | Q(away_team=team_id)
        )
        if status_value:
            queryset = queryset.filter(status=status_value)
        if competition_id:
            queryset = queryset.filter(competition_id=competition_id)
        queryset = queryset.order_by('-match_time')

        rows = []
        for match in queryset:
            is_home = match.home_team_id == team_id
            rows.append({
                'id': match.id,
                'competition_id': match.competition_id,
                'competition_name': match.competition.name if match.competition_id else None,
                'group_type': match.group_type,
                'match_time': match.match_time,
                'venue': match.venue,
                'status': match.status,
                'is_home': is_home,
                'opponent': match.away_team_name if is_home else match.home_team_name,
                'my_score': match.home_score if is_home else match.away_score,
                'opponent_score': match.away_score if is_home else match.home_score,
                'live_link': match.live_link,
            })
        return {'list': rows, 'team_id': team_id}

    def stats(self, user, competition_id: int = None) -> dict:
        profile = self._require_profile(user)
        competition = self._resolve_competition(profile, competition_id)
        empty = {
            'goals': 0, 'assists': 0, 'yellow_cards': 0, 'red_cards': 0, 'appearances': 0,
            'team_record': {'win': 0, 'draw': 0, 'loss': 0},
            'competition_id': competition.pk if competition else None,
            'competition_name': competition.name if competition else None,
        }
        if competition is None:
            # 未报名任何赛事时不跨赛事混算
            return empty

        events = MatchEvent.objects.filter(athlete=profile, match__competition=competition)

        goals = events.filter(type=EventType.GOAL).count()
        # 助攻：进球事件中 related_player_name 命中本人
        assists = events.filter(type=EventType.GOAL, related_player_name=profile.name).count()
        yellow = events.filter(type=EventType.YELLOW).count()
        red = events.filter(type=EventType.RED).count()

        appearances = (
            MatchEvent.objects.filter(
                athlete=profile, match__competition=competition, match__status=MatchStatus.FINISHED,
            )
            .values('match_id')
            .distinct()
            .count()
        )

        record = {'win': 0, 'draw': 0, 'loss': 0}
        if profile.team_id:
            finished = MatchInfo.objects.filter(
                competition=competition, status=MatchStatus.FINISHED,
            ).filter(home_team_id=profile.team_id)
            finished = list(finished) + list(
                MatchInfo.objects.filter(
                    competition=competition, status=MatchStatus.FINISHED, away_team_id=profile.team_id,
                )
            )
            for match in finished:
                is_home = match.home_team_id == profile.team_id
                my = match.home_score if is_home else match.away_score
                opp = match.away_score if is_home else match.home_score
                if my > opp:
                    record['win'] += 1
                elif my == opp:
                    record['draw'] += 1
                else:
                    record['loss'] += 1

        return {
            'goals': goals,
            'assists': assists,
            'yellow_cards': yellow,
            'red_cards': red,
            'appearances': appearances,
            'team_record': record,
            'competition_id': competition.pk,
            'competition_name': competition.name,
        }

    def standings(self, user, competition_id: int = None) -> dict:
        profile = self._require_profile(user)
        team = profile.team
        competition = self._resolve_competition(profile, competition_id)
        if team is None or competition is None:
            return {
                'competition_id': competition.pk if competition else None,
                'competition_name': competition.name if competition else None,
                'group_type': team.group_type if team else None,
                'standings': [],
                'top_scorers': [],
            }

        group_type = team.group_type
        # 参赛球队以该赛事下的报名记录为准，避免把同组别但未报名的球队算进来
        group_teams = Team.objects.filter(
            registrations__competition=competition,
            registrations__status__in=(
                Registration.Status.SUBMITTED,
                Registration.Status.CITY_APPROVED,
                Registration.Status.REPORTED,
                Registration.Status.APPROVED,
            ),
            group_type=group_type,
            status=TeamStatus.APPROVED,
        ).distinct()

        matches = list(
            MatchInfo.objects.filter(
                competition=competition,
                group_type=group_type,
                status=MatchStatus.FINISHED,
                home_team__isnull=False,
                away_team__isnull=False,
            )
        )

        rows = []
        for current in group_teams:
            played = win = draw = loss = gf = ga = 0
            for match in matches:
                if match.home_team_id == current.id:
                    played += 1
                    gf += match.home_score
                    ga += match.away_score
                    if match.home_score > match.away_score:
                        win += 1
                    elif match.home_score == match.away_score:
                        draw += 1
                    else:
                        loss += 1
                elif match.away_team_id == current.id:
                    played += 1
                    gf += match.away_score
                    ga += match.home_score
                    if match.away_score > match.home_score:
                        win += 1
                    elif match.away_score == match.home_score:
                        draw += 1
                    else:
                        loss += 1
            rows.append({
                'team_id': current.id,
                'team_name': current.name,
                'played': played,
                'win': win,
                'draw': draw,
                'loss': loss,
                'gf': gf,
                'ga': ga,
                'gd': gf - ga,
                'points': win * 3 + draw,
                'is_my_team': current.id == team.id,
            })

        rows.sort(key=lambda r: (-r['points'], -r['gd'], -r['gf']))

        match_ids = [match.id for match in matches]
        scorers = (
            MatchEvent.objects.filter(match_id__in=match_ids, type=EventType.GOAL, athlete__isnull=False)
            .values('athlete_id')
            .annotate(goals=Count('id'))
            .order_by('-goals')[:10]
        )
        top_scorers = []
        for row in scorers:
            athlete = AthleteProfile.objects.filter(pk=row['athlete_id']).select_related('team').first()
            if athlete is None:
                continue
            top_scorers.append({
                'athlete_id': athlete.id,
                'name': athlete.name,
                'jersey_no': athlete.jersey_no,
                'team_name': athlete.team.name if athlete.team else None,
                'goals': row['goals'],
            })

        return {
            'competition_id': competition.pk,
            'competition_name': competition.name,
            'group_type': group_type,
            'standings': rows,
            'top_scorers': top_scorers,
        }

    @staticmethod
    def _resolve_competition(profile, competition_id: int = None):
        """确定统计口径：显式赛事优先，否则取本队最近一届已报名赛事。"""
        if competition_id:
            competition = Competition.objects.filter(pk=competition_id).first()
            if competition is None:
                raise BusinessException(ResponseCode.NOT_FOUND, '赛事不存在', status_code=404)
            return competition
        if not profile.team_id:
            return None
        registration = (
            Registration.objects.filter(team_id=profile.team_id)
            .exclude(status=Registration.Status.DRAFT)
            .select_related('competition')
            .order_by('-competition__year', '-competition_id')
            .first()
        )
        return registration.competition if registration else None

    @staticmethod
    def _require_profile(user) -> AthleteProfile:
        profile = user.role_profile()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '运动员资料不存在', status_code=404)
        return profile
