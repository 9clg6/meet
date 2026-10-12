"""
Tests for external API /rooms/{id}/livekit-token endpoint
"""

# pylint: disable=W0621

from datetime import datetime, timedelta, timezone
from unittest import mock

from django.conf import settings

import jwt
import pytest
from lasuite.oidc_resource_server.authentication import ResourceServerAuthentication
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import ApplicationScope, RoleChoices

from .test_external_api_rooms import generate_test_token

pytestmark = pytest.mark.django_db


def decode(token):
    """The claims of a LiveKit token of the test configuration."""
    return jwt.decode(
        token,
        settings.LIVEKIT_CONFIGURATION["api_secret"],
        algorithms=["HS256"],
        options={"verify_aud": False},
    )


def mint(user, room, body, scopes=(ApplicationScope.ROOMS_LIVEKIT_TOKEN,)):
    """Ask a token for `user` in `room` as an application."""
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {generate_test_token(user, list(scopes))}"
    )
    return client.post(
        f"/external-api/v1.0/rooms/{room.id}/livekit-token/", body, format="json"
    )


def test_livekit_token_uses_the_identity_the_application_imposes(settings):
    """The identity is the one of the request, not the sub of the user; the user
    is carried by the attributes, with the Matrix ids."""
    settings.LIVEKIT_CONFIGURATION = {
        **settings.LIVEKIT_CONFIGURATION,
        "url": "http://synapse:7880",
    }
    owner = UserFactory()
    room = RoomFactory(users=[(owner, RoleChoices.OWNER)])
    alice = UserFactory(sub="alice-sub")

    response = mint(
        alice,
        room,
        {
            "identity": "@alice:localhost:DEVICE",
            "username": "Alice",
            "role": "administrator",
        },
    )

    assert response.status_code == 200
    assert response.json()["url"] == "http://synapse:7880"
    assert response.json()["room"] == str(room.id)
    claims = decode(response.json()["token"])
    assert claims["sub"] == "@alice:localhost:DEVICE"
    assert claims["name"] == "Alice"
    assert claims["video"]["room"] == str(room.id)
    assert claims["video"]["roomAdmin"] is True
    assert claims["attributes"]["user_id"] == str(alice.pk)
    assert claims["attributes"]["matrix_user_id"] == "@alice:localhost"
    assert claims["attributes"]["matrix_device_id"] == "DEVICE"
    assert claims["attributes"]["room_role"] == "administrator"


def test_livekit_token_needs_no_role_on_the_room():
    """The application vouches for the user: no ResourceAccess needed."""
    room = RoomFactory()
    alice = UserFactory()

    response = mint(alice, room, {"identity": "@alice:localhost:DEVICE"})

    assert response.status_code == 200
    assert decode(response.json()["token"])["video"]["roomAdmin"] is False


def test_livekit_token_one_user_two_devices_two_identities():
    """The same user from two devices gets two identities: LiveKit keeps both."""
    room = RoomFactory()
    alice = UserFactory()

    first = mint(alice, room, {"identity": "@alice:localhost:A"})
    second = mint(alice, room, {"identity": "@alice:localhost:B"})

    assert decode(first.json()["token"])["sub"] == "@alice:localhost:A"
    assert decode(second.json()["token"])["sub"] == "@alice:localhost:B"


def test_livekit_token_sets_the_sub_of_a_provisioned_user(caplog):
    """A user provisioned by email has no sub: the application gives it, once,
    and the log keeps it."""
    caplog.set_level("INFO")
    room = RoomFactory()
    alice = UserFactory(sub=None)

    mint(alice, room, {"identity": "@alice:localhost:A", "sub": "alice-oidc"})
    alice.refresh_from_db()
    assert alice.sub == "alice-oidc"
    assert f"Sub set via application: user_id={alice.id}" in caplog.text

    mint(alice, room, {"identity": "@alice:localhost:A", "sub": "another"})
    alice.refresh_from_db()
    assert alice.sub == "alice-oidc"


def test_livekit_token_does_not_tell_that_a_sub_exists():
    """A sub another user has is not set, and the answer does not say so."""
    UserFactory(sub="taken")
    room = RoomFactory()
    alice = UserFactory(sub=None)

    response = mint(alice, room, {"identity": "@alice:localhost:A", "sub": "taken"})

    assert response.status_code == 200
    assert set(response.json()) == {"url", "room", "token"}
    alice.refresh_from_db()
    assert alice.sub is None


def test_livekit_token_requires_its_own_scope():
    """rooms:retrieve reads a room: it mints no token."""
    room = RoomFactory()
    alice = UserFactory()

    response = mint(
        alice,
        room,
        {"identity": "@alice:localhost:A"},
        scopes=(ApplicationScope.ROOMS_RETRIEVE, ApplicationScope.ROOMS_LIST),
    )

    assert response.status_code == 403


def test_livekit_token_refuses_the_oidc_token_of_a_user():
    """Applications only: a resource server token, which an end user holds,
    does not reach the route, whatever its scopes."""
    owner = UserFactory()
    room = RoomFactory(users=[(owner, RoleChoices.OWNER)])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer an-oidc-access-token")

    with mock.patch.object(
        ResourceServerAuthentication,
        "authenticate",
        return_value=(owner, "an-oidc-access-token"),
    ) as authenticate:
        response = client.post(
            f"/external-api/v1.0/rooms/{room.id}/livekit-token/",
            {"identity": "@owner:localhost:A", "role": "owner"},
            format="json",
        )

    assert response.status_code == 401
    authenticate.assert_not_called()


def test_livekit_token_refuses_an_addons_token():
    """Applications only: a token signed for the addons does not reach the route."""
    alice = UserFactory()
    room = RoomFactory()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": settings.ADDONS_TOKEN_ISSUER,
            "aud": settings.ADDONS_TOKEN_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(seconds=60),
            "scope": ApplicationScope.ROOMS_LIVEKIT_TOKEN,
            "user_id": str(alice.id),
        },
        settings.ADDONS_TOKEN_SECRET_KEY,
        algorithm=settings.ADDONS_TOKEN_ALG,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.post(
        f"/external-api/v1.0/rooms/{room.id}/livekit-token/",
        {"identity": "@alice:localhost:A"},
        format="json",
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "identity",
    ["", "alice", "@alice:localhost", "alice:localhost:DEVICE", "@alice:local host:A"],
)
def test_livekit_token_refuses_an_identity_of_another_shape(identity):
    """The identity is `@user:server:device`, nothing else."""
    room = RoomFactory()
    alice = UserFactory()

    response = mint(alice, room, {"identity": identity})

    assert response.status_code == 400


def test_livekit_token_follows_the_sources_of_the_room():
    """A member publishes what the room allows; an administrator, everything."""
    room = RoomFactory(configuration={"can_publish_sources": ["microphone"]})
    alice = UserFactory()

    member = mint(alice, room, {"identity": "@alice:localhost:A"})
    administrator = mint(
        alice, room, {"identity": "@alice:localhost:A", "role": "administrator"}
    )

    assert decode(member.json()["token"])["video"]["canPublishSources"] == [
        "microphone"
    ]
    assert (
        decode(administrator.json()["token"])["video"]["canPublishSources"]
        == settings.LIVEKIT_DEFAULT_SOURCES
    )


def test_livekit_token_is_logged(caplog):
    """The log keeps who got a token, for which room, never the token."""
    caplog.set_level("INFO")
    room = RoomFactory()
    alice = UserFactory()

    response = mint(alice, room, {"identity": "@alice:localhost:A", "role": "member"})

    assert (
        f"room_id={room.id}, user_id={alice.id}, "
        "identity=@alice:localhost:A, role=member" in caplog.text
    )
    assert response.json()["token"] not in caplog.text


def test_livekit_token_requires_an_identity():
    """Without identity, 400."""
    room = RoomFactory()
    alice = UserFactory()

    assert mint(alice, room, {}).status_code == 400


def test_livekit_token_unknown_room():
    """A room that does not exist: 404."""
    alice = UserFactory()
    token = generate_test_token(alice, [ApplicationScope.ROOMS_LIVEKIT_TOKEN])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.post(
        "/external-api/v1.0/rooms/00000000-0000-0000-0000-000000000000/livekit-token/",
        {"identity": "@alice:localhost:A"},
        format="json",
    )

    assert response.status_code == 404
