"""运动员端业务逻辑（对应 Laravel AthleteService）。"""
from django.db.models import Count

from apps.common.codes import ResponseCode
from apps.common.exceptions import BusinessException
from apps.accounts.models import AthleteProfile
from apps.live.models import EventType, MatchEvent, MatchStatus
from apps.live.models import Match as MatchInfo
from apps.org.models import Team, TeamStatus


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
        if data.get('name'):
            profile.name = data['name']
            user.name = data['name']
            user.save(update_fields=['name'])
        if 'jersey_no' in data:
            profile.jersey_no = data['jersey_no']
        if 'position' in data:
            profile.position = data['position']
        profile.save()
        return self.profile(user)

    def matches(self, user, status_value: str = None) -> dict:
        profile = self._require_profile(user)
        team_id = profile.team_id
        if not team_id:
            return {'list': [], 'team_id': None}

        from django.db.models import Q

        queryset = MatchInfo.objects.filter(Q(home_team=team_id) | Q(away_team=team_id))
        if status_value:
            queryset = queryset.filter(status=status_value)
        queryset = queryset.order_by('-match_time')

        rows = []
        for match in queryset:
            is_home = match.home_team_id == team_id
            rows.append({
                'id': match.id,
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

    def stats(self, user) -> dict:
        profile = self._require_profile(user)
        events = MatchEvent.objects.filter(athlete=profile)

        goals = events.filter(type=EventType.GOAL).count()
        # 助攻：进球事件中 related_player_name 命中本人
        assists = events.filter(type=EventType.GOAL, related_player_name=profile.name).count()
        yellow = events.filter(type=EventType.YELLOW).count()
        red = events.filter(type=EventType.RED).count()

        appearances = (
            MatchEvent.objects.filter(athlete=profile, match__status=MatchStatus.FINISHED)
            .values('match_id')
            .distinct()
            .count()
        )

        record = {'win': 0, 'draw': 0, 'loss': 0}
        if profile.team_id:
            finished = MatchInfo.objects.filter(
                home_team_id=profile.team_id
            ).filter(status=MatchStatus.FINISHED)
            finished = list(finished) + list(
                MatchInfo.objects.filter(away_team_id=profile.team_id, status=MatchStatus.FINISHED)
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
        }

    def standings(self, user) -> dict:
        profile = self._require_profile(user)
        team = profile.team
        if team is None:
            return {'standings': [], 'top_scorers': [], 'group_type': None}

        group_type = team.group_type
        group_teams = Team.objects.filter(group_type=group_type, status=TeamStatus.APPROVED)

        matches = list(
            MatchInfo.objects.filter(
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

        return {'group_type': group_type, 'standings': rows, 'top_scorers': top_scorers}

    @staticmethod
    def _require_profile(user) -> AthleteProfile:
        profile = user.role_profile()
        if profile is None:
            raise BusinessException(ResponseCode.NOT_FOUND, '运动员资料不存在', status_code=404)
        return profile
