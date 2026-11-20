"""身份证唯一约束 + 空串归一化 + 队内球衣号唯一（对应合并分支的模型状态）。"""

from django.db import migrations, models


def normalize_blank_id_cards(apps, schema_editor):
    """空身份证统一为 NULL，避免普通唯一索引把空串当成重复值。"""
    AthleteProfile = apps.get_model('accounts', 'AthleteProfile')
    CoachProfile = apps.get_model('accounts', 'CoachProfile')
    AthleteProfile.objects.filter(id_card='').update(id_card=None)
    CoachProfile.objects.filter(id_card='').update(id_card=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_alter_user_role_countyprofile"),
    ]

    operations = [
        migrations.RunPython(normalize_blank_id_cards, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="athleteprofile",
            name="id_card",
            field=models.CharField(
                blank=True,
                max_length=18,
                null=True,
                unique=True,
                verbose_name="身份证号",
            ),
        ),
        migrations.AlterField(
            model_name="coachprofile",
            name="id_card",
            field=models.CharField(
                blank=True,
                max_length=18,
                null=True,
                unique=True,
                verbose_name="身份证号",
            ),
        ),
        migrations.AddConstraint(
            model_name="athleteprofile",
            constraint=models.UniqueConstraint(
                fields=("team", "jersey_no"), name="uniq_athlete_team_jersey"
            ),
        ),
    ]
