"""预置四川省及 21 个市州（对应 Laravel ProvinceSeeder）。"""
from django.db import migrations

SICHUAN = ('510000', '四川省')
CITIES = [
    ('510100', '成都市'),
    ('510300', '自贡市'),
    ('510400', '攀枝花市'),
    ('510500', '泸州市'),
    ('510600', '德阳市'),
    ('510700', '绵阳市'),
    ('510800', '广元市'),
    ('510900', '遂宁市'),
    ('511000', '内江市'),
    ('511100', '乐山市'),
    ('511300', '南充市'),
    ('511400', '眉山市'),
    ('511500', '宜宾市'),
    ('511600', '广安市'),
    ('511700', '达州市'),
    ('511800', '雅安市'),
    ('511900', '巴中市'),
    ('512000', '资阳市'),
    ('513200', '阿坝藏族羌族自治州'),
    ('513300', '甘孜藏族自治州'),
    ('513400', '凉山彝族自治州'),
]


def seed(apps, schema_editor):
    Province = apps.get_model('org', 'Province')
    City = apps.get_model('org', 'City')

    province, _ = Province.objects.get_or_create(code=SICHUAN[0], defaults={'name': SICHUAN[1]})
    for code, name in CITIES:
        City.objects.get_or_create(province=province, code=code, defaults={'name': name})


def unseed(apps, schema_editor):
    Province = apps.get_model('org', 'Province')
    Province.objects.filter(code=SICHUAN[0]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('org', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
