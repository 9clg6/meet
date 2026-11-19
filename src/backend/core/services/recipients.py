"""Who receives a meeting's recording, transcript and summary."""

import logging

from django.db import transaction

from asgiref.sync import async_to_sync
from livekit.api import ListParticipantsRequest  # pylint: disable=no-name-in-module

from core import models, utils

logger = logging.getLogger(__name__)

_TRACKED_RECORDING_STATUSES = (
    models.RecordingStatusChoices.INITIATED,
    models.RecordingStatusChoices.ACTIVE,
)


def room_owner(room):
    """The room's oldest owner."""
    access = (
        room.accesses.filter(role=models.RoleChoices.OWNER, user__isnull=False)
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    return access.user if access else None


def recording_starter(recording):
    """The user who started the recording: the one with the oldest access."""
    access = (
        recording.accesses.filter(user__isnull=False)
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    return access.user if access else None


def recording_recipient(recording):
    """The room owner, else the user who started the recording."""
    return room_owner(recording.room) or recording_starter(recording)


def attendees_with_email(user_ids, exclude=None):
    """The active users among ``user_ids`` who have an email, but ``exclude``."""
    ids = [str(i) for i in dict.fromkeys(user_ids or []) if utils.is_uuid(str(i))]
    users = (
        models.User.objects.filter(id__in=ids, is_active=True)
        .exclude(email__isnull=True)
        .exclude(email="")
    )
    if exclude is not None:
        users = users.exclude(id=exclude.id)
    return list(users.order_by("email"))


@async_to_sync
async def _list_participants(room_name):
    lkapi = utils.create_livekit_client()
    try:
        response = await lkapi.room.list_participants(
            ListParticipantsRequest(room=room_name)
        )
        return list(response.participants)
    finally:
        await lkapi.aclose()


def present_users(room):
    """The ids of the signed-in participants in the room right now."""
    room_name = str(room.id)
    try:
        participants = _list_participants(room_name)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Could not list the participants of room %s", room.id, exc_info=True
        )
        return []
    ids = (
        utils.proven_user_id(room_name, p.identity, dict(p.attributes))
        for p in participants
    )
    return list(dict.fromkeys(i for i in ids if i))


def _add(attendees, user_id):
    if user_id in attendees:
        return False
    attendees.append(user_id)
    return True


def note_attendee(room_name, user_id):
    """Add a participant who joined to the attendees of a shared summary."""
    if not utils.is_uuid(room_name):
        return
    with transaction.atomic():
        room = models.Room.objects.select_for_update().filter(pk=room_name).first()
        if room is None:
            return
        run = (room.configuration or {}).get("linto") or {}
        if run.get("share_summary") and _add(run.setdefault("attendees", []), user_id):
            room.save(update_fields=["configuration"])
        recordings = models.Recording.objects.select_for_update().filter(
            room=room, status__in=_TRACKED_RECORDING_STATUSES
        )
        for recording in recordings:
            if not (recording.options or {}).get("share_summary"):
                continue
            state = recording.linto_state or {}
            if _add(state.setdefault("attendees", []), user_id):
                recording.linto_state = state
                recording.save(update_fields=["linto_state", "updated_at"])
