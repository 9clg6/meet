"""Give the applications that minted LiveKit tokens before the scope existed the new one."""

from django.db import migrations

RETRIEVE_SCOPE = "rooms:retrieve"
MINT_SCOPE = "rooms:livekit_token"


def grant(apps, schema_editor):
    """livekit-token asked for rooms:retrieve until 0024: keep those applications working."""
    Application = apps.get_model("core", "Application")
    for application in Application.objects.filter(
        scopes__contains=[RETRIEVE_SCOPE]
    ).exclude(scopes__contains=[MINT_SCOPE]):
        application.scopes = [*application.scopes, MINT_SCOPE]
        application.save(update_fields=["scopes"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_room_created_by_application"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
