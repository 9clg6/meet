"""Tests for core.services.recipients: who receives a meeting's deliveries."""

# pylint: disable=protected-access

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from core import factories, models
from core.services import recipients
from core.services.livekit_events import LiveKitEventsService
from core.utils import user_id_proof

pytestmark = pytest.mark.django_db


def _participant(room, user, identity="identity-1", proof=None):
    attributes = {"user_id": str(user.id)}
    attributes["user_id_proof"] = proof or user_id_proof(
        str(room.id), identity, str(user.id)
    )
    return SimpleNamespace(identity=identity, attributes=attributes)


class TestRoomOwner:
    """The room owner is the oldest owner of the room."""

    def test_the_oldest_owner(self):
        """Admins aside, the first owner wins."""
        room = factories.RoomFactory()
        first = factories.UserFactory()
        second = factories.UserFactory()
        admin = factories.UserFactory()
        room.accesses.create(user=admin, role=models.RoleChoices.ADMIN)
        late = room.accesses.create(user=second, role=models.RoleChoices.OWNER)
        early = room.accesses.create(user=first, role=models.RoleChoices.OWNER)
        models.ResourceAccess.objects.filter(pk=early.pk).update(
            created_at=late.created_at - timedelta(days=1)
        )
        assert recipients.room_owner(room) == first

    def test_none_without_owner(self):
        """No owner, no one."""
        room = factories.RoomFactory()
        room.accesses.create(user=factories.UserFactory(), role="administrator")
        assert recipients.room_owner(room) is None


class TestRecordingRecipient:
    """A recording goes to the room owner, else its starter."""

    def test_the_room_owner_rather_than_the_starter(self):
        """The starter is known but the owner receives."""
        owner = factories.UserFactory()
        starter = factories.UserFactory()
        room = factories.RoomFactory(users=[(owner, models.RoleChoices.OWNER)])
        recording = factories.RecordingFactory(room=room)
        recording.grant_initial_accesses(starter=starter)
        assert recipients.recording_starter(recording) == starter
        assert recipients.recording_recipient(recording) == owner

    def test_the_starter_when_the_room_has_no_owner(self):
        """Without room owner the starter receives."""
        starter = factories.UserFactory()
        recording = factories.RecordingFactory()
        recording.grant_initial_accesses(starter=starter)
        assert recipients.recording_recipient(recording) == starter


class TestAttendeesWithEmail:
    """Only reachable attendees are emailed."""

    def test_active_users_with_email_but_the_excluded_one(self):
        """Duplicates, invalid ids and unreachable users are dropped."""
        alice = factories.UserFactory(email="alice@example.com")
        owner = factories.UserFactory(email="owner@example.com")
        no_email = factories.UserFactory(email=None)
        inactive = factories.UserFactory(email="x@example.com", is_active=False)
        ids = [str(u.id) for u in (alice, owner, no_email, inactive, alice)]
        assert recipients.attendees_with_email([*ids, "not-a-uuid"], exclude=owner) == [
            alice
        ]


class TestPresentUsers:
    """The participants present are known by their proven user id."""

    def test_only_proven_users(self):
        """Forged, anonymous and duplicate participants are dropped."""
        room = factories.RoomFactory()
        alice = factories.UserFactory()
        bob = factories.UserFactory()
        participants = [
            _participant(room, alice),
            _participant(room, bob, identity="bob", proof="forged"),
            SimpleNamespace(identity="guest", attributes={}),
            _participant(room, alice, identity="alice-2"),
        ]
        with mock.patch.object(
            recipients, "_list_participants", return_value=participants
        ):
            assert recipients.present_users(room) == [str(alice.id)]

    def test_livekit_failure_is_no_one(self):
        """LiveKit unreachable: no attendee, no error."""
        room = factories.RoomFactory()
        with mock.patch.object(
            recipients, "_list_participants", side_effect=RuntimeError("down")
        ):
            assert not recipients.present_users(room)


class TestNoteAttendee:
    """A participant who joins is noted where a summary is shared."""

    def test_a_live_run_sharing_its_summary(self):
        """Added once to the live run."""
        room = factories.RoomFactory(
            configuration={"linto": {"share_summary": True, "attendees": ["a"]}}
        )
        recipients.note_attendee(str(room.id), "b")
        recipients.note_attendee(str(room.id), "b")
        room.refresh_from_db()
        assert room.configuration["linto"]["attendees"] == ["a", "b"]

    def test_a_live_run_without_sharing(self):
        """Nothing noted when the summary is not shared."""
        room = factories.RoomFactory(configuration={"linto": {"summary": True}})
        recipients.note_attendee(str(room.id), "b")
        room.refresh_from_db()
        assert "attendees" not in room.configuration["linto"]

    def test_an_active_recording_sharing_its_summary(self):
        """Added to the recording sharing its summary."""
        room = factories.RoomFactory()
        shared = factories.RecordingFactory(
            room=room,
            status=models.RecordingStatusChoices.ACTIVE,
            options={"share_summary": True},
        )
        recipients.note_attendee(str(room.id), "b")
        shared.refresh_from_db()
        assert shared.linto_state == {"attendees": ["b"]}

    @pytest.mark.parametrize(
        "status,options",
        [
            (models.RecordingStatusChoices.ACTIVE, {}),
            (models.RecordingStatusChoices.STOPPED, {"share_summary": True}),
        ],
    )
    def test_other_recordings_are_left_alone(self, status, options):
        """Not shared or not running: untouched."""
        room = factories.RoomFactory()
        recording = factories.RecordingFactory(
            room=room, status=status, options=options
        )
        recipients.note_attendee(str(room.id), "b")
        recording.refresh_from_db()
        assert recording.linto_state == {}

    def test_an_unknown_room(self):
        """An unknown room name is ignored."""
        recipients.note_attendee("not-a-room", "b")
        recipients.note_attendee("00000000-0000-0000-0000-000000000000", "b")


class TestParticipantJoinedWebhook:
    """The webhook notes signed-in participants only."""

    def test_a_proven_user_is_noted(self):
        """The proven user id is noted."""
        room = factories.RoomFactory()
        user = factories.UserFactory()
        data = SimpleNamespace(
            room=SimpleNamespace(name=str(room.id)),
            participant=_participant(room, user),
        )
        with mock.patch("core.services.livekit_events.note_attendee") as note_attendee:
            LiveKitEventsService._handle_participant_joined(data)
        note_attendee.assert_called_once_with(str(room.id), str(user.id))

    def test_an_unproven_participant_is_ignored(self):
        """A forged proof is ignored."""
        room = factories.RoomFactory()
        user = factories.UserFactory()
        data = SimpleNamespace(
            room=SimpleNamespace(name=str(room.id)),
            participant=_participant(room, user, proof="forged"),
        )
        with mock.patch("core.services.livekit_events.note_attendee") as note_attendee:
            LiveKitEventsService._handle_participant_joined(data)
        note_attendee.assert_not_called()
