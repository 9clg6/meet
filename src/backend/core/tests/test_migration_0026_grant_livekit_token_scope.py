"""Test the migration that gives the livekit_token scope to the applications of before."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

import pytest

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("core", "0025_room_created_by_application")]
AFTER = [("core", "0026_grant_livekit_token_scope")]


def test_migration_0026_grants_the_scope_to_applications_that_retrieve_rooms():
    """An application with rooms:retrieve gets rooms:livekit_token; the others do not."""
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    Application = executor.loader.project_state(BEFORE).apps.get_model(
        "core", "Application"
    )
    retriever = Application.objects.create(
        name="retriever",
        client_id="retriever",
        scopes=["rooms:create", "rooms:retrieve"],
    )
    creator = Application.objects.create(
        name="creator", client_id="creator", scopes=["rooms:create"]
    )

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(AFTER)

    retriever.refresh_from_db()
    creator.refresh_from_db()
    assert retriever.scopes == ["rooms:create", "rooms:retrieve", "rooms:livekit_token"]
    assert creator.scopes == ["rooms:create"]
