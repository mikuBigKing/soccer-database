"""球员遴选档案、初选/复选评分与最佳球员结果。"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SelectionProfile(models.Model):
    """一名球员在一届赛事下的遴选档案（按赛事唯一）。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待评'
        QUALIFIED = 'qualified', '晋级复选'
        ELIMINATED = 'eliminated', '未晋级'

    competition = models.ForeignKey(
        'registration.Competition', on_delete=models.PROTECT, related_name='selection_profiles'
    )
    athlete = models.ForeignKey(
        'accounts.AthleteProfile', on_delete=models.PROTECT, related_name='selection_profiles'
    )
    team = models.ForeignKey(
        'org.Team', null=True, blank=True, on_delete=models.SET_NULL, related_name='selection_profiles'
    )
    group_type = models.CharField('组别', max_length=20, db_index=True)
    name_snapshot = models.CharField('姓名快照', max_length=50, blank=True, default='')
    id_card_snapshot = models.CharField('身份证快照', max_length=18, blank=True, default='', db_index=True)
    status = models.CharField('状态', max_length=20, choices=Status.choices, default=Status.PENDING)
    created_by = models.BigIntegerField('创建人 user_id', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'selection_profiles'
        ordering = ('-id',)
        constraints = [
            models.UniqueConstraint(fields=['competition', 'athlete'], name='uniq_selection_competition_athlete')
        ]
        indexes = [
            models.Index(fields=['competition', 'group_type']),
            models.Index(fields=['team']),
        ]


class SelectionScore(models.Model):
    """初选/复选评分：进球 25%、防守 25%、助攻 20%、犯规 30%。"""

    class Stage(models.TextChoices):
        INITIAL = 'initial', '初选'
        FINAL = 'final', '复选'

    profile = models.ForeignKey(SelectionProfile, on_delete=models.CASCADE, related_name='scores')
    stage = models.CharField('阶段', max_length=10, choices=Stage.choices, db_index=True)
    goals_score = models.PositiveSmallIntegerField(
        '进球评分', default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    defense_score = models.PositiveSmallIntegerField(
        '防守评分', default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    assist_score = models.PositiveSmallIntegerField(
        '助攻评分', default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    foul_score = models.PositiveSmallIntegerField(
        '犯规评分', default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    total_score = models.DecimalField('加权总分', max_digits=5, decimal_places=2, default=0)
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    remark = models.CharField('备注', max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'selection_scores'
        ordering = ('-total_score', '-id')
        constraints = [
            models.UniqueConstraint(fields=['profile', 'stage'], name='uniq_selection_profile_stage')
        ]
        indexes = [models.Index(fields=['stage', 'total_score'])]


class SelectionBestResult(models.Model):
    """最佳球员遴选结果（按赛事 + 组别 + 名次唯一）。"""

    competition = models.ForeignKey(
        'registration.Competition', on_delete=models.PROTECT, related_name='selection_best'
    )
    group_type = models.CharField('组别', max_length=20, db_index=True)
    athlete = models.ForeignKey(
        'accounts.AthleteProfile', on_delete=models.PROTECT, related_name='selection_best'
    )
    profile = models.ForeignKey(
        SelectionProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name='best_results'
    )
    rank = models.PositiveIntegerField('名次')
    total_score = models.DecimalField('复选总分', max_digits=5, decimal_places=2, default=0)
    published = models.BooleanField('已公示', default=False, db_index=True)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'selection_best_results'
        ordering = ('group_type', 'rank')
        constraints = [
            models.UniqueConstraint(
                fields=['competition', 'group_type', 'rank'], name='uniq_selection_best_rank'
            )
        ]
