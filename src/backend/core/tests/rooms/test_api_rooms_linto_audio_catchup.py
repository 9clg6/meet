"""
Test the LinTO audio catch-up route of rooms: ``linto/audio-catchup``.

Auth = a LiveKit room token in an `X-LiveKit-Token` Authorization header. The
service layer is mocked.
"""

# pylint: disable=W0621

from unittest import mock

import pytest
from livekit.api import AccessToken, VideoGrants
from rest_framework.test import APIClient

from core.authentication.livekit import LIVEKIT_AUTH_SCHEME
from core.factories import RoomFactory
from core.services import linto_catchup

pytestmark = pytest.mark.django_db

LK_KEY = "devkey"
LK_SECRET = "secret"


@pytest.fixture(autouse=True)
def linto_settings(settings):
    """LinTO on, LiveKit configured."""
    settings.LINTO_FEATURE_ENABLED = True
    settings.LIVEKIT_CONFIGURATION = {
        "api_key": LK_KEY,
        "api_secret": LK_SECRET,
        "url": "ws://livekit:7880",
    }
    return settings


def _room_token(room, identity="guest-1"):
    grants = VideoGrants(room=str(room.id), room_join=True)
    return (
        AccessToken(api_key=LK_KEY, api_secret=LK_SECRET)
        .with_identity(identity)
        .with_grants(grants)
        .to_jwt()
    )


def _url(room, query=""):
    return f"/api/v1.0/rooms/{room.id}/linto/audio-catchup/{query}"


def _auth(room):
    return {"HTTP_AUTHORIZATION": f"{LIVEKIT_AUTH_SCHEME} {_room_token(room)}"}


def test_feature_off_is_a_404(settings):
    """Without LinTO the route does not exist."""
    settings.LINTO_FEATURE_ENABLED = False
    room = RoomFactory()
    res = APIClient().post(_url(room), {}, format="json", **_auth(room))
    assert res.status_code == 404


def test_no_token_is_unauthorized():
    """The room token is required."""
    room = RoomFactory()
    res = APIClient().post(_url(room), {}, format="json")
    assert res.status_code in (401, 403)


def test_a_token_in_the_body_is_ignored():
    """The token only counts in the Authorization header."""
    room = RoomFactory()
    res = APIClient().post(_url(room), {"token": _room_token(room)}, format="json")
    assert res.status_code in (401, 403)


def test_post_asks_for_the_brief_of_the_caller():
    """POST asks the service for the brief of the token's identity."""
    room = RoomFactory()
    with mock.patch.object(
        linto_catchup,
        "request_brief",
        return_value={"state": "transcribing", "recording_id": "r1"},
    ) as request_brief:
        res = APIClient().post(
            _url(room),
            {"joined_at_ms": 1762250400000},
            format="json",
            **_auth(room),
        )
    assert res.status_code == 200
    assert res.json() == {"state": "transcribing", "recording_id": "r1"}
    args, kwargs = request_brief.call_args
    assert args[1] == "guest-1"
    assert kwargs == {"client_joined_at_ms": 1762250400000}


def test_post_with_an_invalid_join_time_is_a_400():
    """A join time that is not a number is refused."""
    room = RoomFactory()
    res = APIClient().post(
        _url(room), {"joined_at_ms": "soon"}, format="json", **_auth(room)
    )
    assert res.status_code == 400


def test_post_outside_the_room_is_a_403():
    """A token holder who is not in the room is refused."""
    room = RoomFactory()
    with mock.patch.object(
        linto_catchup,
        "request_brief",
        side_effect=linto_catchup.NotInRoomError("absent"),
    ):
        res = APIClient().post(_url(room), {}, format="json", **_auth(room))
    assert res.status_code == 403


def test_a_linto_failure_is_unavailable():
    """LinTO down: the front shows the catch-up as unavailable."""
    room = RoomFactory()
    with mock.patch.object(
        linto_catchup,
        "request_brief",
        side_effect=linto_catchup.CatchupError("down"),
    ):
        res = APIClient().post(_url(room), {}, format="json", **_auth(room))
    assert res.status_code == 200
    assert res.json() == {"state": "unavailable", "reason": "linto_error"}


def test_get_needs_the_recording():
    """GET reads the brief of one recording: its id is required."""
    room = RoomFactory()
    res = APIClient().get(_url(room), **_auth(room))
    assert res.status_code == 400


def test_get_checks_presence_then_reads_the_brief():
    """GET answers a participant in the room only."""
    room = RoomFactory()
    with (
        mock.patch(
            "core.api.viewsets.ParticipantsManagement.check_if_in_meeting",
            return_value=True,
        ),
        mock.patch.object(
            linto_catchup, "request_status", return_value={"state": "ready"}
        ) as request_status,
    ):
        res = APIClient().get(_url(room, "?recording_id=rec"), **_auth(room))
    assert res.status_code == 200
    assert res.json() == {"state": "ready"}
    request_status.assert_called_once_with(room, "rec")

    with mock.patch(
        "core.api.viewsets.ParticipantsManagement.check_if_in_meeting",
        return_value=False,
    ):
        res = APIClient().get(_url(room, "?recording_id=rec"), **_auth(room))
    assert res.status_code == 403
