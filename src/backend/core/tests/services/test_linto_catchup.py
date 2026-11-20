"""Tests of the LinTO catch-up without live transcription (fork)."""

# pylint: disable=W0621,W0212,E1101,W0613,too-many-lines

from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache

import pytest
from livekit import api

from core.factories import RecordingFactory
from core.services import linto_catchup
from core.services.livekit_events import LiveKitEventsService

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
ROUTE = "visio-catchup-brief"


@pytest.fixture(autouse=True)
def catchup_settings(settings):
    """Feature on, Studio configured, deterministic durations."""
    settings.LINTO_CATCHUP_ENABLED = True
    settings.LINTO_STUDIO_BASE_URL = "http://studio-api"
    settings.LINTO_CATCHUP_PREFIX = "catchup"
    settings.LINTO_CATCHUP_SEGMENT_DURATION_S = 10
    settings.LINTO_CATCHUP_LATE_THRESHOLD_S = 30
    settings.LINTO_CATCHUP_SUMMARY_SERVICE = ROUTE
    settings.LINTO_CATCHUP_REUSE_S = 120
    settings.LINTO_CATCHUP_MIN_CHARS = 20
    settings.LINTO_CATCHUP_JOB_TIMEOUT_S = 1800
    settings.LINTO_ENTITLEMENTS_ENABLED = False
    settings.LIVEKIT_CONFIGURATION = {
        "api_key": "key",
        "api_secret": "secret",
        "url": "https://livekit.example.com/",
    }
    return settings


def _playlist(start, names, durations=None):
    lines = ["#EXTM3U", "#EXT-X-VERSION:4", "#EXT-X-TARGETDURATION:10"]
    t = start
    for name in names:
        duration = (durations or {}).get(name, 10.0)
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{t.isoformat().replace('+00:00', 'Z')}")
        lines.append(f"#EXTINF:{duration:.3f},")
        lines.append(name)
        t += timedelta(seconds=duration)
    return "\n".join(lines)


def _egress(prefix, egress_id="EG_catchup", started_at=None):
    info = api.EgressInfo(egress_id=egress_id)
    info.room_composite.segment_outputs.append(
        api.SegmentedFileOutput(filename_prefix=prefix)
    )
    if started_at is not None:
        info.started_at = int(started_at.timestamp() * 1_000_000_000)
    return info


def _segments(count):
    return [
        {
            "name": f"seg_{i:05d}.ts",
            "start": T0 + timedelta(seconds=10 * i),
            "duration_ms": 10000,
            "key": f"catchup/r/seg_{i:05d}.ts",
        }
        for i in range(count)
    ]


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


# ── Pure helpers ────────────────────────────────────────────────────────────


def test_parse_playlist():
    """Names, wall-clock starts and durations are read from the playlist."""
    segments = linto_catchup.parse_playlist(
        _playlist(T0, ["seg_00000.ts", "seg_00001.ts"], {"seg_00001.ts": 4.5})
    )
    assert [s["name"] for s in segments] == ["seg_00000.ts", "seg_00001.ts"]
    assert segments[0]["start"] == T0
    assert segments[1]["start"] == T0 + timedelta(seconds=10)
    assert segments[1]["duration_ms"] == 4500


def test_catchup_prefix_detection():
    """Only an egress writing under the catch-up prefix is a catch-up egress."""
    info = _egress("catchup/abc/seg")
    assert linto_catchup.catchup_segment_prefix(info) == "catchup/abc/seg"
    assert linto_catchup.recording_id_of("catchup/abc/seg") == "abc"
    assert linto_catchup.catchup_segment_prefix(api.EgressInfo(egress_id="x")) is None
    assert linto_catchup.catchup_segment_prefix(_egress("recordings/abc")) is None


@pytest.mark.parametrize(
    ("mode", "options", "eligible"),
    (
        ("transcript", {"catchup": True}, True),
        ("transcript", {}, False),
        ("transcript", {"catchup": False}, False),
        ("screen_recording", {"transcribe": True, "catchup": True}, True),
        ("screen_recording", {"original_mode": "transcript", "catchup": True}, True),
        ("screen_recording", {"catchup": True}, False),
    ),
)
def test_is_eligible(mode, options, eligible):
    """Only transcribed recordings whose starter left the option on."""
    recording = RecordingFactory(mode=mode, options=options)
    assert linto_catchup.is_eligible(recording) is eligible


def test_list_segments_merges_playlists_and_listing():
    """The main playlist lags; the live one and the bucket listing fill in."""
    main = _playlist(T0, ["seg_00000.ts", "seg_00001.ts"])
    live = _playlist(T0 + timedelta(seconds=20), ["seg_00002.ts"])
    objects = {"catchup/r1/index.m3u8": main, "catchup/r1/live.m3u8": live}
    listing = ["seg_00000.ts", "seg_00001.ts", "seg_00002.ts", "seg_00003.ts"]
    with (
        mock.patch.object(linto_catchup, "_read_object", side_effect=objects.get),
        mock.patch.object(linto_catchup, "_list_segment_objects", return_value=listing),
    ):
        segments = linto_catchup.list_segments("r1")

    assert [s["name"] for s in segments] == listing
    assert segments[3]["start"] == T0 + timedelta(seconds=30)  # inferred
    assert segments[3]["key"] == "catchup/r1/seg_00003.ts"


def test_segments_between_keeps_what_is_not_sent_yet():
    """Midpoint after what Meet holds, and not after the arrival."""
    with mock.patch.object(linto_catchup, "list_segments", return_value=_segments(10)):
        first = linto_catchup.segments_between("r", None, T0 + timedelta(seconds=65))
        delta = linto_catchup.segments_between(
            "r", T0 + timedelta(seconds=40), T0 + timedelta(seconds=95)
        )
    assert [s["name"] for s in first] == [f"seg_{i:05d}.ts" for i in range(6)]
    assert [s["name"] for s in delta] == [f"seg_{i:05d}.ts" for i in range(4, 9)]


def test_read_segments_concatenates_in_order():
    """MPEG-TS segments of one stream are concatenated in their order."""
    data = {"a": b"A" * 3, "b": b"B", "c": b"CC"}
    with mock.patch.object(linto_catchup, "_read_bytes", side_effect=data.get):
        assert linto_catchup.read_segments(["a", "b", "c"]) == b"AAABCC"
    assert linto_catchup.read_segments([]) == b""


def test_live_run_active():
    """Only a live run of its own that offers the catch-up serves late joiners."""
    room = RecordingFactory().room
    assert linto_catchup.live_run_active(room) is False
    room.configuration = {"linto": {"session_id": "s1"}}
    assert linto_catchup.live_run_active(room) is True
    room.configuration = {"linto": {"session_id": "s1", "captions_only": True}}
    assert linto_catchup.live_run_active(room) is False
    room.configuration = {"linto": {"session_id": "s1", "catchup": False}}
    assert linto_catchup.live_run_active(room) is False


# ── Late joiner: request_brief ─────────────────────────────────────────────


@pytest.fixture
def active_transcript():
    """An active transcript recording whose starter left the option on."""
    return RecordingFactory(
        mode="transcript",
        status="active",
        options={"language": "fr", "catchup": True},
    )


@pytest.fixture(autouse=True)
def no_summary_breaker():
    """No "summary service unavailable" left by another test."""
    cache.delete(linto_catchup.SUMMARY_UNAVAILABLE_KEY)
    yield
    cache.delete(linto_catchup.SUMMARY_UNAVAILABLE_KEY)


FIRST_TURN = {"turn_id": "t0", "segment": "first", "words": [{"stime": 0.5}]}


@pytest.fixture
def studio():
    """A fake Studio client."""
    client = mock.MagicMock()
    client.upload.return_value = "conv-1"
    client.first_turn.return_value = dict(FIRST_TURN)
    with mock.patch.object(linto_catchup, "StudioClient", return_value=client):
        yield client


def _arrive(recording, joined_at, segments=12, **kwargs):
    capture = _egress(f"catchup/{recording.id}/seg", started_at=T0)
    with (
        mock.patch.object(linto_catchup, "find_capture", return_value=capture),
        mock.patch.object(
            linto_catchup,
            "_participant_joined_at_ms",
            return_value=int(joined_at.timestamp() * 1000),
        ),
        mock.patch.object(
            linto_catchup, "list_segments", return_value=_segments(segments)
        ),
        mock.patch.object(
            linto_catchup,
            "read_segments",
            side_effect=lambda keys: b"".join(k.encode() for k in keys),
        ) as read,
    ):
        body = linto_catchup.request_brief(recording.room, "alice", **kwargs)
    return body, read


def test_first_late_joiner_sends_the_audio_up_to_arrival(active_transcript, studio):
    """The minutes before the arrival go to Studio, without diarization."""
    body, read = _arrive(active_transcript, T0 + timedelta(seconds=65))

    assert body["state"] == "transcribing"
    assert body["recording_id"] == str(active_transcript.id)
    keys = read.call_args.args[0]
    assert keys == [f"catchup/r/seg_{i:05d}.ts" for i in range(6)]
    audio = studio.upload.call_args.args[0]
    assert audio == b"".join(k.encode() for k in keys)
    assert studio.upload.call_args.kwargs["language"] == "fr"
    job = linto_catchup.load_state(active_transcript.id)["job"]
    assert job["conversation_id"] == "conv-1"
    assert job["up_to"] == _iso(T0 + timedelta(seconds=60))
    assert job["audio_s"] == 60


def test_not_late_gets_nothing(active_transcript, studio):
    """A participant present from the start gets nothing; Studio is not called."""
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=5))
    assert body["state"] == "none"
    studio.upload.assert_not_called()


def test_client_can_only_ask_for_less(active_transcript, studio):
    """After a reload, the earlier client join time wins (min)."""
    client_ms = int((T0 + timedelta(seconds=65)).timestamp() * 1000)
    _arrive(
        active_transcript,
        T0 + timedelta(seconds=300),
        client_joined_at_ms=client_ms,
    )
    job = linto_catchup.load_state(active_transcript.id)["job"]
    assert job["up_to"] == _iso(T0 + timedelta(seconds=60))


def test_an_arrival_attaches_to_the_running_job(active_transcript, studio):
    """While a job runs, a new arrival waits for it: no second upload."""
    _arrive(active_transcript, T0 + timedelta(seconds=65))
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=95))
    assert body["state"] == "transcribing"
    assert studio.upload.call_count == 1


def test_a_recent_brief_is_reused(active_transcript, studio):
    """A brief covering up to two minutes before the arrival answers at once."""
    linto_catchup.save_state(
        active_transcript.id,
        {
            "transcript": "x" * 50,
            "covered_until": _iso(T0 + timedelta(seconds=60)),
            "brief": {
                "text": "Brief",
                "covered_until": _iso(T0 + timedelta(seconds=60)),
            },
            "job": None,
            "outcome": "ready",
        },
    )
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=150))
    assert body["state"] == "ready"
    assert body["brief"]["text"] == "Brief"
    studio.upload.assert_not_called()


def test_a_later_arrival_sends_only_the_delta(active_transcript, studio):
    """Only the minutes after what Meet holds are sent."""
    linto_catchup.save_state(
        active_transcript.id,
        {
            "transcript": "x" * 50,
            "covered_until": _iso(T0 + timedelta(seconds=40)),
            "brief": {"text": "Old", "covered_until": _iso(T0 + timedelta(seconds=40))},
            "job": None,
            "outcome": "ready",
        },
    )
    _, read = _arrive(active_transcript, T0 + timedelta(seconds=300), segments=12)
    assert read.call_args.args[0] == [f"catchup/r/seg_{i:05d}.ts" for i in range(4, 12)]


def test_another_arrival_sending_says_sending(active_transcript, studio):
    """Someone else is uploading: answer "sending", start nothing."""
    cache.add(linto_catchup._lock_key(active_transcript.id, "start"), 1, 60)
    try:
        body, _ = _arrive(active_transcript, T0 + timedelta(seconds=65))
    finally:
        cache.delete(linto_catchup._lock_key(active_transcript.id, "start"))
    assert body["state"] == "sending"
    studio.upload.assert_not_called()


def test_nothing_recorded_yet_is_too_short(active_transcript, studio):
    """No segment before the arrival and no brief: too short."""
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=65), segments=0)
    assert body["state"] == "too_short"
    studio.upload.assert_not_called()


def test_without_capture_the_catchup_is_unavailable(active_transcript, studio):
    """No capture running: unavailable, Studio is not called."""
    with mock.patch.object(linto_catchup, "find_capture", return_value=None):
        body = linto_catchup.request_brief(active_transcript.room, "alice")
    assert body["state"] == "unavailable"
    studio.upload.assert_not_called()


def test_without_recording_there_is_nothing():
    """No transcribed recording: nothing to offer."""
    recording = RecordingFactory(mode="screen_recording", status="active")
    assert linto_catchup.request_brief(recording.room, "alice")["state"] == "none"


def test_a_live_run_of_its_own_serves_instead(active_transcript, studio):
    """A live run with its catch-up serves the late joiners itself."""
    room = active_transcript.room
    room.configuration = {"linto": {"session_id": "s1", "org_id": "o1"}}
    room.save()
    assert linto_catchup.request_brief(room, "alice") == {"state": "live"}
    studio.upload.assert_not_called()


def test_live_captions_leave_the_brief_to_the_recording(active_transcript, studio):
    """Live captions on top of the recording keep nothing: the audio brief runs."""
    room = active_transcript.room
    room.configuration = {"linto": {"session_id": "s1", "captions_only": True}}
    room.save()
    active_transcript.refresh_from_db()
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=65))
    assert body["state"] == "transcribing"


def test_the_owner_needs_the_summary_capability(active_transcript, settings):
    """No summary right for the owner: no capture, no brief."""
    with mock.patch.object(linto_catchup, "summary_entitled", return_value=False):
        assert linto_catchup.is_eligible(active_transcript) is False
        assert linto_catchup.request_brief(active_transcript.room, "alice") == {
            "state": "none"
        }


def test_a_participant_not_in_the_room_is_refused(active_transcript):
    """Someone not in the room is refused."""
    capture = _egress(f"catchup/{active_transcript.id}/seg", started_at=T0)
    with (
        mock.patch.object(linto_catchup, "find_capture", return_value=capture),
        mock.patch.object(
            linto_catchup, "_participant_joined_at_ms", return_value=None
        ),
        pytest.raises(linto_catchup.NotInRoomError),
    ):
        linto_catchup.request_brief(active_transcript.room, "mallory")


# ── The job, one step per poll: request_status ─────────────────────────────


def _running(recording, step="transcribing", transcript="", brief=None, **job):
    linto_catchup.save_state(
        recording.id,
        {
            "transcript": transcript,
            "covered_until": None,
            "brief": brief,
            "job": {
                "conversation_id": "conv-1",
                "from": _iso(T0),
                "up_to": _iso(T0 + timedelta(seconds=60)),
                "step": step,
                "started_at": linto_catchup._iso(linto_catchup._now()),
                "summary_started_at": linto_catchup._iso(linto_catchup._now()),
                "audio_s": 60,
                "errors": 0,
                **job,
            },
            "outcome": None,
        },
    )


def _conversation(state, *segments):
    return {
        "jobs": {"transcription": {"state": state}},
        "text": [{"turn_id": f"t{i}", "segment": s} for i, s in enumerate(segments)],
    }


def _status(recording):
    return linto_catchup.request_status(recording.room, str(recording.id))


def test_a_running_transcription_is_reported(active_transcript, studio):
    """While Studio transcribes, the state says so with an estimate."""
    _running(active_transcript)
    studio.conversation.return_value = _conversation("processing")
    body = _status(active_transcript)
    assert body["state"] == "transcribing"
    assert 0 < body["progress"] < 1
    assert body["eta_s"][0] <= body["eta_s"][1]
    studio.trigger_summary.assert_not_called()


def test_first_transcript_asks_for_the_summary(active_transcript, studio):
    """The first transcript is kept, then summarized as it is."""
    _running(active_transcript)
    studio.conversation.return_value = _conversation(
        "done", "Bonjour à tous, on commence.", "Le budget est validé."
    )
    body = _status(active_transcript)

    assert body["state"] == "summarizing"
    studio.prepend.assert_not_called()
    studio.trigger_summary.assert_called_once_with("conv-1", ROUTE)
    state = linto_catchup.load_state(active_transcript.id)
    assert state["transcript"] == "Bonjour à tous, on commence.\nLe budget est validé."
    assert state["covered_until"] == _iso(T0 + timedelta(seconds=60))


def test_a_later_transcript_gets_the_previous_text_at_its_head(
    active_transcript, studio
):
    """Only the new minutes were transcribed; the text Meet held goes first."""
    _running(active_transcript, transcript="Ce qui a été dit avant.")
    studio.conversation.return_value = _conversation("done", "La suite de la réunion.")
    _status(active_transcript)

    studio.prepend.assert_called_once_with(
        "conv-1", "Ce qui a été dit avant.", FIRST_TURN
    )
    state = linto_catchup.load_state(active_transcript.id)
    assert state["transcript"] == "Ce qui a été dit avant.\nLa suite de la réunion."


def test_too_little_said_is_too_short(active_transcript, studio):
    """Nothing worth summarizing: too short, the conversation goes."""
    _running(active_transcript)
    studio.conversation.return_value = _conversation("done", "Oui.")
    body = _status(active_transcript)
    assert body["state"] == "too_short"
    studio.trigger_summary.assert_not_called()
    studio.delete.assert_called_once_with("conv-1")


def test_a_failed_transcription_fails_the_job(active_transcript, studio):
    """Studio failed to transcribe: failed, the conversation goes."""
    _running(active_transcript)
    studio.conversation.return_value = _conversation("error")
    assert _status(active_transcript)["state"] == "failed"
    studio.delete.assert_called_once_with("conv-1")


def test_the_summary_becomes_the_brief(active_transcript, studio):
    """Once summarized, the brief is kept and the conversation deleted."""
    _running(active_transcript, step="summarizing", transcript="x" * 50)
    studio.summary.return_value = ("done", "### En cours\nLe budget.")
    body = _status(active_transcript)

    assert body["state"] == "ready"
    assert body["brief"] == {
        "text": "### En cours\nLe budget.",
        "covered_until": _iso(T0 + timedelta(seconds=60)),
    }
    studio.delete.assert_called_once_with("conv-1")
    assert linto_catchup.load_state(active_transcript.id)["job"] is None


def test_a_running_summary_is_reported(active_transcript, studio):
    """The summary runs: summarizing."""
    _running(active_transcript, step="summarizing")
    studio.summary.return_value = ("running", None)
    assert _status(active_transcript)["state"] == "summarizing"
    studio.delete.assert_not_called()


def test_a_failed_summary_keeps_the_previous_brief(active_transcript, studio):
    """The summary failed: the previous brief still serves."""
    brief = {"text": "Old", "covered_until": _iso(T0)}
    _running(active_transcript, step="summarizing", brief=brief)
    studio.summary.return_value = ("error", None)
    body = _status(active_transcript)
    assert body["state"] == "ready"
    assert body["brief"]["text"] == "Old"


def test_repeated_studio_errors_fail_the_job(active_transcript, studio):
    """Studio keeps failing: the job is given up after a few polls."""
    _running(active_transcript)
    studio.conversation.side_effect = linto_catchup.CatchupError("down")
    for _ in range(linto_catchup.MAX_STEP_ERRORS - 1):
        assert _status(active_transcript)["state"] == "transcribing"
    assert _status(active_transcript)["state"] == "failed"


def test_a_job_past_its_timeout_fails(active_transcript, studio, settings):
    """A job running too long is given up."""
    settings.LINTO_CATCHUP_JOB_TIMEOUT_S = 1
    _running(active_transcript, started_at=_iso(T0))
    assert _status(active_transcript)["state"] == "failed"
    studio.delete.assert_called_once_with("conv-1")


def test_one_poll_advances_at_a_time(active_transcript, studio):
    """Another poll is advancing the job: answer without a Studio call."""
    _running(active_transcript)
    key = linto_catchup._lock_key(active_transcript.id, "step")
    cache.add(key, 1, 60)
    try:
        assert _status(active_transcript)["state"] == "transcribing"
    finally:
        cache.delete(key)
    studio.conversation.assert_not_called()


def test_status_of_another_room_recording_is_none(active_transcript):
    """A recording of another room is never read."""
    other = RecordingFactory(mode="transcript", status="active")
    assert linto_catchup.request_status(other.room, str(active_transcript.id)) == {
        "state": "none"
    }


# ── The summary service missing or failing ─────────────────────────────────


def _done_transcript(
    studio, text="La suite de la réunion, assez longue pour un résumé."
):
    studio.conversation.return_value = _conversation("done", text)


def test_a_missing_summary_service_ends_the_job_at_once_with_a_warning(
    active_transcript, studio, caplog
):
    """Studio cannot run the summary service (missing on the gateway, no
    gateway): the job ends at the first poll, a warning names the service, and
    the text Meet took is kept once."""
    _running(active_transcript)
    _done_transcript(studio)
    studio.trigger_summary.side_effect = linto_catchup.StudioHTTPError(
        "Studio POST /conversations/conv-1/download: HTTP 502",
        502,
        "Request failed with status code 404",
    )
    with caplog.at_level("WARNING", logger="core.services.linto_catchup"):
        body = _status(active_transcript)

    assert body["state"] == "failed"
    studio.delete.assert_called_once_with("conv-1")
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        ROUTE in m and "Request failed with status code 404" in m and "UPGRADE.md" in m
        for m in warnings
    ), warnings
    assert ROUTE in linto_catchup.summary_unavailable()
    state = linto_catchup.load_state(active_transcript.id)
    assert state["transcript"] == "La suite de la réunion, assez longue pour un résumé."


def test_after_a_summary_failure_new_arrivals_send_nothing(active_transcript, studio):
    """While the summary service is known to fail, a late joiner gets
    "unavailable" at once: no audio is sent nor transcribed."""
    cache.set(linto_catchup.SUMMARY_UNAVAILABLE_KEY, f"{ROUTE}: HTTP 502", 300)
    body, read = _arrive(active_transcript, T0 + timedelta(seconds=65))
    assert body == {
        "state": "unavailable",
        "reason": "summary_service",
        "recording_id": str(active_transcript.id),
    }
    read.assert_not_called()
    studio.upload.assert_not_called()


def test_a_ready_brief_is_still_served_after_a_summary_failure(
    active_transcript, studio
):
    """A brief already written still serves while the service fails."""
    cache.set(linto_catchup.SUMMARY_UNAVAILABLE_KEY, f"{ROUTE}: HTTP 502", 300)
    linto_catchup.save_state(
        active_transcript.id,
        {
            "transcript": "x" * 50,
            "covered_until": _iso(T0 + timedelta(seconds=60)),
            "brief": {
                "text": "Brief",
                "covered_until": _iso(T0 + timedelta(seconds=60)),
            },
            "job": None,
            "outcome": "ready",
        },
    )
    body, _ = _arrive(active_transcript, T0 + timedelta(seconds=150))
    assert body["state"] == "ready"
    assert body["brief"]["text"] == "Brief"


def test_a_replayed_step_adds_nothing_twice(active_transcript, studio):
    """Studio unreachable when asked for the summary: the next poll retries
    the request only, the text and the head turn are not added again."""
    _running(active_transcript, transcript="Ce qui a été dit avant.")
    _done_transcript(studio)
    studio.trigger_summary.side_effect = [
        linto_catchup.CatchupError("Studio unreachable: timeout"),
        None,
    ]
    assert _status(active_transcript)["state"] == "transcribing"
    assert _status(active_transcript)["state"] == "summarizing"

    assert studio.prepend.call_count == 1
    assert studio.first_turn.call_count == 1
    assert studio.trigger_summary.call_count == 2
    state = linto_catchup.load_state(active_transcript.id)
    assert state["transcript"] == (
        "Ce qui a été dit avant.\nLa suite de la réunion, assez longue pour un résumé."
    )


def test_a_failed_summary_job_marks_the_service_unavailable(
    active_transcript, studio, caplog
):
    """The gateway job itself failed: a warning with its error, and the
    service is marked unavailable for a while."""
    _running(active_transcript, step="summarizing")
    studio.summary.return_value = ("error", "model not found")
    with caplog.at_level("WARNING", logger="core.services.linto_catchup"):
        assert _status(active_transcript)["state"] == "failed"
    assert any("model not found" in r.getMessage() for r in caplog.records)
    assert linto_catchup.summary_unavailable()


def test_a_studio_error_carries_its_detail():
    """Studio's error message is kept in the exception (for the warning)."""
    with (
        mock.patch.object(linto_catchup.StudioClient, "_token", return_value="tok"),
        mock.patch.object(
            linto_catchup.requests,
            "request",
            return_value=_response(
                502, {"status": "error", "error": "Service not found: x"}
            ),
        ),
        pytest.raises(linto_catchup.StudioHTTPError) as raised,
    ):
        linto_catchup.StudioClient().trigger_summary("c1", ROUTE)
    assert raised.value.status == 502
    assert raised.value.detail == "Service not found: x"
    assert "Service not found: x" in str(raised.value)


# ── Studio client ───────────────────────────────────────────────────────────


def _response(status=200, body=None):
    response = mock.MagicMock(status_code=status)
    response.json.return_value = body
    return response


@pytest.fixture
def http():
    """requests.request, with a fixed credential."""
    with (
        mock.patch.object(linto_catchup.StudioClient, "_token", return_value="tok"),
        mock.patch.object(linto_catchup.requests, "request") as request,
    ):
        yield request


def test_prepend_keeps_the_first_turn_words(http):
    """The first turn takes the text with its own words; the original follows."""
    first = {"turn_id": "t0", "segment": "Bonjour", "words": [{"stime": 0.5}]}
    http.side_effect = [
        _response(200, {"text": [first, {"turn_id": "t1", "segment": "Suite"}]}),
        _response(200, {}),
        _response(200, {}),
    ]
    client = linto_catchup.StudioClient()
    client.prepend("c1", "Avant", client.first_turn("c1"))

    patch_call, post_call = http.call_args_list[1], http.call_args_list[2]
    assert patch_call.args == (
        "PATCH",
        "http://studio-api/api/conversations/c1/turns/t0",
    )
    assert patch_call.kwargs["json"] == {**first, "segment": "Avant"}
    assert post_call.args == ("POST", "http://studio-api/api/conversations/c1/turns/t0")
    assert post_call.kwargs["json"] == {"segment": "Bonjour", "words": [{"stime": 0.5}]}


def test_summary_reads_the_export_of_the_service(http):
    """The export of the catch-up service is read once done."""
    http.side_effect = [
        _response(
            200,
            [
                {"format": "other", "status": "done", "jobId": "j0"},
                {"format": ROUTE, "status": "done", "jobId": "j1"},
            ],
        ),
        _response(200, {"status": "success", "content": " Brief "}),
    ]
    assert linto_catchup.StudioClient().summary("c1", ROUTE) == ("done", "Brief")
    assert (
        http.call_args_list[1].args[1].endswith("/conversations/c1/export/j1/content")
    )


@pytest.mark.parametrize(
    ("exports", "status"),
    (
        ([], "running"),
        ([{"format": ROUTE, "status": "processing"}], "running"),
        ([{"format": ROUTE, "status": "error"}], "error"),
    ),
)
def test_summary_states(http, exports, status):
    """No export yet or still running: running; an error: error."""
    http.return_value = _response(200, exports)
    assert linto_catchup.StudioClient().summary("c1", ROUTE)[0] == status


def test_trigger_summary_names_the_service(http):
    """The summary is asked of the catch-up service by its route."""
    http.return_value = _response(200, {})
    linto_catchup.StudioClient().trigger_summary("c1", ROUTE)
    assert http.call_args.args == (
        "POST",
        "http://studio-api/api/conversations/c1/download",
    )
    assert http.call_args.kwargs["params"] == {"format": ROUTE}


def test_delete_tolerates_a_missing_conversation(http):
    """A conversation already gone is fine; another error is raised."""
    http.return_value = _response(404)
    linto_catchup.StudioClient().delete("c1")
    http.return_value = _response(500)
    with pytest.raises(linto_catchup.CatchupError):
        linto_catchup.StudioClient().delete("c1")


def test_a_rejected_credential_is_renewed_once():
    """A 401 renews the credential and retries once."""
    with (
        mock.patch.object(
            linto_catchup.StudioClient, "_token", side_effect=["old", "new"]
        ) as token,
        mock.patch.object(
            linto_catchup.requests,
            "request",
            side_effect=[_response(401), _response(200, {"jobs": {}})],
        ) as request,
    ):
        assert linto_catchup.StudioClient().conversation("c1") == {"jobs": {}}
    assert token.call_args_list == [mock.call(False), mock.call(True)]
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer new"}


def test_studio_unreachable_is_a_catchup_error(http):
    """A network error becomes a CatchupError."""
    http.side_effect = linto_catchup.requests.ConnectionError("down")
    with pytest.raises(linto_catchup.CatchupError):
        linto_catchup.StudioClient().conversation("c1")


# ── End of the recording, reconciler ───────────────────────────────────────


def test_the_end_of_the_recording_drops_everything(active_transcript, studio):
    """The capture stops, a running job's conversation and the state go."""
    _running(active_transcript, transcript="x")
    with mock.patch.object(linto_catchup, "stop_captures") as stop:
        linto_catchup.on_recording_ended(active_transcript)
    stop.assert_called_once_with(str(active_transcript.room_id), active_transcript.id)
    studio.delete.assert_called_once_with("conv-1")
    assert linto_catchup.load_state(active_transcript.id)["transcript"] == ""


def test_reconcile_discards_the_state_of_ended_recordings(studio):
    """A state left behind by an ended recording is discarded."""
    ended = RecordingFactory(mode="transcript", status="stopped")
    active = RecordingFactory(mode="transcript", status="active")
    _running(ended)
    _running(active)
    with (
        mock.patch.object(linto_catchup, "_list_active_egresses", return_value=[]),
        mock.patch.object(linto_catchup, "list_prefixes", return_value=[]),
    ):
        result = linto_catchup.reconcile()
    assert result["discarded"] >= 1
    assert linto_catchup.load_state(ended.id)["job"] is None
    assert linto_catchup.load_state(active.id)["job"] is not None


# ── Segments deletion task ─────────────────────────────────────────────────


def test_segments_go_once_the_capture_stopped_even_if_the_recording_reads_active(
    active_transcript,
):
    """After its egress ended, a recording reads "active" until its file is
    seen in the storage: its segments still go once the capture stopped."""
    from core.tasks.linto_catchup import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        delete_linto_catchup_segments,
    )

    with (
        mock.patch.object(linto_catchup, "find_capture", return_value=None),
        mock.patch.object(linto_catchup, "delete_segments") as delete,
    ):
        delete_linto_catchup_segments(str(active_transcript.id))
    delete.assert_called_once_with(str(active_transcript.id))


def test_segments_stay_while_the_capture_runs(active_transcript):
    """A capture still writing keeps its segments."""
    from core.tasks.linto_catchup import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        delete_linto_catchup_segments,
    )

    with (
        mock.patch.object(linto_catchup, "find_capture", return_value=_egress("x")),
        mock.patch.object(linto_catchup, "delete_segments") as delete,
    ):
        delete_linto_catchup_segments(str(active_transcript.id))
    delete.assert_not_called()


# ── Webhooks ────────────────────────────────────────────────────────────────


@pytest.fixture
def events_service():
    """LiveKit events service."""
    return LiveKitEventsService()


@mock.patch.object(api.WebhookReceiver, "receive")
def test_catchup_egress_events_are_acknowledged(mock_receive, events_service):
    """The side capture is not a Recording: its events must not fail (500)."""
    data = api.WebhookEvent(event="egress_updated")
    data.egress_info.CopyFrom(_egress("catchup/r1/seg"))
    data.egress_info.room_name = "11111111-1111-1111-1111-111111111111"
    mock_receive.return_value = data
    request = mock.MagicMock(headers={"Authorization": "token"}, body=b"{}")

    events_service.receive(request)  # no ActionFailedError


@mock.patch("core.services.room_management.RoomManagement.update_metadata")
@mock.patch.object(linto_catchup, "start_capture")
def test_main_egress_active_starts_capture(mock_start, _metadata, events_service):
    """An audio transcript recording reaching ACTIVE starts the capture at once."""
    recording = RecordingFactory(
        worker_id="EG_main",
        mode="transcript",
        status="active",
        options={"catchup": True},
    )
    data = mock.MagicMock()
    data.egress_info.egress_id = "EG_main"
    data.egress_info.status = api.EgressStatus.EGRESS_ACTIVE

    events_service._handle_egress_updated(data)

    mock_start.assert_called_once_with(recording)


@mock.patch("core.services.room_management.RoomManagement.update_metadata")
@mock.patch.object(linto_catchup, "start_capture")
def test_plain_screen_recording_starts_nothing(mock_start, _metadata, events_service):
    """A video recording without transcription gets no capture."""
    RecordingFactory(worker_id="EG_main", mode="screen_recording", status="active")
    data = mock.MagicMock()
    data.egress_info.egress_id = "EG_main"
    data.egress_info.status = api.EgressStatus.EGRESS_ACTIVE

    events_service._handle_egress_updated(data)

    mock_start.assert_not_called()


@mock.patch("core.services.room_management.RoomManagement.update_metadata")
@mock.patch.object(linto_catchup, "on_recording_ended")
def test_main_egress_ended_stops_capture(mock_ended, _metadata, events_service):
    """Whatever the reason the main egress ends, the capture stops with it."""
    recording = RecordingFactory(
        worker_id="EG_main", mode="transcript", status="active"
    )
    data = mock.MagicMock()
    data.egress_info.egress_id = "EG_main"
    data.egress_info.status = api.EgressStatus.EGRESS_FAILED
    # The recording's own end is not under test here.
    events_service.recording_events = mock.MagicMock()

    events_service._handle_egress_ended(data)

    mock_ended.assert_called_once()
    assert mock_ended.call_args.args[0].id == recording.id


@mock.patch("core.services.room_management.RoomManagement.update_metadata")
@mock.patch.object(linto_catchup, "start_capture")
def test_recording_without_the_option_starts_nothing(
    mock_start, _metadata, events_service
):
    """The starter turned the catch-up option off: no capture at all."""
    RecordingFactory(worker_id="EG_main", mode="transcript", status="active")
    data = mock.MagicMock()
    data.egress_info.egress_id = "EG_main"
    data.egress_info.status = api.EgressStatus.EGRESS_ACTIVE

    events_service._handle_egress_updated(data)

    mock_start.assert_not_called()
