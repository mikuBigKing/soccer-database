"""去掉身份证号的库级唯一约束：唯一性由应用层校验（已软删档案不占用）。"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_athlete_id_card_unique_and_jersey"),
    ]

    operations = [
        migrations.AlterField(
            model_name="athleteprofile",
            name="id_card",
            field=models.CharField(
                blank=True, max_length=18, null=True, verbose_name="身份证号"
            ),
        ),
        migrations.AlterField(
            model_name="coachprofile",
            name="id_card",
            field=models.CharField(
                blank=True, max_length=18, null=True, verbose_name="身份证号"
            ),
        ),
    ]
