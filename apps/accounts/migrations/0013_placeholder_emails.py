"""存量账号补齐占位邮箱：登录标识统一为邮箱后，无邮箱账号用占位地址兜底。"""

from django.db import migrations


def fill_placeholder_emails(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.filter(email__isnull=True).iterator():
        user.email = f'{user.phone}@placeholder.local' if user.phone else f'user{user.id}@placeholder.local'
        user.save(update_fields=['email'])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_user_email_verified_at_verificationcode"),
    ]

    operations = [
        migrations.RunPython(fill_placeholder_emails, migrations.RunPython.noop),
    ]
