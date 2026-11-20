"""LinTO catch-up without live transcription (fork).

While a transcribed recording runs and its starter left the catch-up option on,
a side audio-only egress writes the room's audio as 10 s HLS segments into
Meet's bucket, under ``<LINTO_CATCHUP_PREFIX>/<recording id>/``. Nothing is
processed during the meeting.

When a participant joins late, Meet sends Studio only the minutes it has not
sent yet, through the standard API the deferred transcription already uses
(an upload without diarization), and keeps the returned text. Before asking for
the summary, it puts the text it already held at the head of that conversation,
so the LLM Gateway service (``LINTO_CATCHUP_SUMMARY_SERVICE``) summarizes the
whole meeting. It keeps the brief and deletes the conversation. Every exchange
starts from Meet; the state lives in Meet's cache, one entry per recording.

Meet's own recording is never touched: its OGG output cannot carry segments,
and a segment upload failure would end it.
"""

# pylint: disable=no-member,too-many-lines

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage

import botocore
import requests
from asgiref.sync import async_to_sync
from livekit import api as livekit_api

from core import models, utils
from core.entitlements.capabilities import summary_entitled

logger = logging.getLogger(__name__)

STUDIO_TIMEOUT = 15
UPLOAD_TIMEOUT = 120
CREDENTIAL_CACHE_KEY = "linto_catchup:studio_credential"
CREDENTIAL_CACHE_TTL = 600

STATE_PREFIX = "linto_catchup:state:"
STATE_TTL = 12 * 3600
START_LOCK_TTL = UPLOAD_TIMEOUT + 30
STEP_LOCK_TTL = 60
MAX_STEP_ERRORS = 5
SUMMARY_ETA_S = (5, 60)
# After the summary service failed, new jobs wait this long (no audio sent nor
# transcribed for a brief that cannot be written).
SUMMARY_UNAVAILABLE_KEY = "linto_catchup:summary_unavailable"
SUMMARY_RETRY_S = 300

PLAYLIST_NAME = "index.m3u8"
LIVE_PLAYLIST_NAME = "live.m3u8"
SEGMENT_PREFIX = "seg"
_SEGMENT_RE = re.compile(r"^seg_(\d+)\.ts$")
READ_WORKERS = 8

# States in which the browser keeps polling.
RUNNING_STATES = ("sending", "transcribing", "summarizing")


class CatchupError(Exception):
    """A catch-up operation fails (LiveKit, storage or Studio)."""


class NotInRoomError(CatchupError):
    """The caller is not a participant of the room."""


class StudioHTTPError(CatchupError):
    """Studio answered with an error status (not a network failure)."""

    def __init__(self, message: str, status: int, detail: str):
        super().__init__(message)
        self.status = status
        self.detail = detail


# ── Eligibility and naming ──────────────────────────────────────────────────


def is_enabled() -> bool:
    """The feature is on for this deployment."""
    return bool(settings.LINTO_CATCHUP_ENABLED and settings.LINTO_STUDIO_BASE_URL)


def is_eligible(recording: models.Recording) -> bool:
    """A recording whose audio will be transcribed, whose starter left the
    catch-up option on, and whose owner may have it summarized: the brief is an
    LLM summary, metered like the end-of-meeting one."""
    options = recording.options or {}
    transcribed = (
        recording.mode == models.RecordingModeChoices.TRANSCRIPT
        or options.get("original_mode") == models.RecordingModeChoices.TRANSCRIPT
        or bool(options.get("transcribe"))
    )
    if not transcribed or not options.get("catchup"):
        return False
    return summary_entitled(recording_owner(recording))


def recording_owner(recording: models.Recording):
    """The owner of the recording, whose rights the brief is produced under."""
    access = (
        recording.accesses.filter(role=models.RoleChoices.OWNER)
        .select_related("user")
        .first()
    )
    return access.user if access else None


def live_run_active(room) -> bool:
    """A live run of its own that offers the catch-up serves the late joiners
    (``linto/catchup``). Live captions on top of a recording keep nothing, and
    a run whose option is off offers nothing: the recording's brief serves."""
    run = (room.configuration or {}).get("linto")
    return bool(run) and not run.get("captions_only") and run.get("catchup", True)


def recording_prefix(recording_id) -> str:
    """Object prefix of a recording's catch-up segments (no trailing slash)."""
    return f"{settings.LINTO_CATCHUP_PREFIX.strip('/')}/{recording_id}"


def _root_prefix() -> str:
    return f"{settings.LINTO_CATCHUP_PREFIX.strip('/')}/"


def catchup_segment_prefix(egress_info) -> Optional[str]:
    """The catch-up prefix an egress writes to, or None for any other egress."""
    request = egress_info.room_composite
    for output in request.segment_outputs:
        if output.filename_prefix.startswith(_root_prefix()):
            return output.filename_prefix
    return None


def recording_id_of(prefix: str) -> Optional[str]:
    """Recording id from a catch-up segment prefix ``catchup/<id>/seg``."""
    parts = prefix[len(_root_prefix()) :].split("/")
    return parts[0] if parts and parts[0] else None


# ── Capture lifecycle (LiveKit egress) ─────────────────────────────────────


def _bucket_args() -> dict:
    return {
        "endpoint": settings.AWS_S3_ENDPOINT_URL,
        "access_key": settings.AWS_S3_ACCESS_KEY_ID,
        "secret": settings.AWS_S3_SECRET_ACCESS_KEY,
        "region": settings.AWS_S3_REGION_NAME,
        "bucket": settings.AWS_STORAGE_BUCKET_NAME,
        "force_path_style": True,
    }


@async_to_sync
async def _list_active_egresses(room_name: Optional[str] = None):
    lkapi = utils.create_livekit_client()
    try:
        kwargs = {"active": True}
        if room_name:
            kwargs["room_name"] = room_name
        response = await lkapi.egress.list_egress(
            livekit_api.ListEgressRequest(**kwargs)
        )
        return list(response.items)
    except livekit_api.TwirpError as e:
        raise CatchupError(f"LiveKit list_egress failed: {e.message}") from e
    finally:
        await lkapi.aclose()


@async_to_sync
async def _start_segments_egress(room_name: str, prefix: str):
    lkapi = utils.create_livekit_client()
    try:
        output = livekit_api.SegmentedFileOutput(
            filename_prefix=f"{prefix}/{SEGMENT_PREFIX}",
            playlist_name=f"{prefix}/{PLAYLIST_NAME}",
            live_playlist_name=f"{prefix}/{LIVE_PLAYLIST_NAME}",
            segment_duration=settings.LINTO_CATCHUP_SEGMENT_DURATION_S,
            s3=livekit_api.S3Upload(**_bucket_args()),
        )
        request = livekit_api.RoomCompositeEgressRequest(
            room_name=room_name, audio_only=True, segment_outputs=[output]
        )
        return await lkapi.egress.start_room_composite_egress(request)
    except livekit_api.TwirpError as e:
        raise CatchupError(f"LiveKit start egress failed: {e.message}") from e
    finally:
        await lkapi.aclose()


@async_to_sync
async def _stop_egress(egress_id: str):
    lkapi = utils.create_livekit_client()
    try:
        await lkapi.egress.stop_egress(
            livekit_api.StopEgressRequest(egress_id=egress_id)
        )
    except livekit_api.TwirpError as e:
        logger.warning("Catch-up egress %s stop failed: %s", egress_id, e.message)
    finally:
        await lkapi.aclose()


def find_capture(room_name: str, recording_id) -> Optional[object]:
    """The active catch-up egress of a recording, if any."""
    prefix = recording_prefix(recording_id)
    for info in _list_active_egresses(room_name):
        seg_prefix = catchup_segment_prefix(info)
        if seg_prefix and seg_prefix.startswith(f"{prefix}/"):
            return info
    return None


def start_capture(recording: models.Recording) -> Optional[str]:
    """Start the side segmented egress of a recording. Idempotent: webhooks
    can be delivered twice. Returns the egress id, or None when not started."""
    if not is_enabled() or not is_eligible(recording):
        return None
    if recording.status != models.RecordingStatusChoices.ACTIVE:
        return None
    room_name = str(recording.room_id)
    existing = find_capture(room_name, recording.id)
    if existing is not None:
        return existing.egress_id
    info = _start_segments_egress(room_name, recording_prefix(recording.id))
    logger.info(
        "Catch-up capture %s started for recording %s", info.egress_id, recording.id
    )
    return info.egress_id


def stop_captures(room_name: str, recording_id=None) -> int:
    """Stop the catch-up egresses of a room (of one recording if given)."""
    stopped = 0
    for info in _list_active_egresses(room_name):
        seg_prefix = catchup_segment_prefix(info)
        if not seg_prefix:
            continue
        if recording_id and recording_id_of(seg_prefix) != str(recording_id):
            continue
        _stop_egress(info.egress_id)
        stopped += 1
        logger.info("Catch-up capture %s stopped (room %s)", info.egress_id, room_name)
    return stopped


def on_recording_ended(recording: models.Recording):
    """Main egress ended, whatever the reason: the banner disappears, so the
    capture must stop now, and what Meet holds for the late joiners goes (a
    running job's conversation in Studio, the text and the brief). The caller
    deletes the segments a minute later, once a running upload has read them."""
    stop_captures(str(recording.room_id), recording.id)
    discard_state(recording.id)


def reconcile() -> dict:
    """Stop captures whose recording is no longer active, delete orphan
    prefixes and the state of ended recordings. Run every minute by a CronJob
    (`manage.py linto_catchup_reconcile`), never by Meet's Celery beat, which a
    deferred transcription can hold."""
    stopped, deleted, discarded = 0, 0, 0
    active_ids = set()
    for info in _list_active_egresses():
        seg_prefix = catchup_segment_prefix(info)
        if not seg_prefix:
            continue
        recording_id = recording_id_of(seg_prefix)
        if _is_active(recording_id):
            active_ids.add(recording_id)
            continue
        _stop_egress(info.egress_id)
        stopped += 1
    for recording_id in list_prefixes():
        if recording_id in active_ids or _is_active(recording_id):
            continue
        delete_segments(recording_id)
        deleted += 1
    for recording_id in _state_recording_ids():
        if _is_active(recording_id):
            continue
        discard_state(recording_id)
        discarded += 1
    return {"stopped": stopped, "deleted": deleted, "discarded": discarded}


def _is_active(recording_id) -> bool:
    return models.Recording.objects.filter(
        id=recording_id, status=models.RecordingStatusChoices.ACTIVE
    ).exists()


# ── Segments in Meet's bucket ──────────────────────────────────────────────


def _s3():
    return default_storage.connection.meta.client


def _read_object(key: str) -> Optional[str]:
    try:
        obj = _s3().get_object(Bucket=default_storage.bucket_name, Key=key)
    except botocore.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise CatchupError(f"S3 read failed for {key}") from e
    return obj["Body"].read().decode("utf-8")


def _read_bytes(key: str) -> bytes:
    try:
        obj = _s3().get_object(Bucket=default_storage.bucket_name, Key=key)
    except botocore.exceptions.ClientError as e:
        raise CatchupError(f"S3 read failed for {key}") from e
    return obj["Body"].read()


def read_segments(keys: list) -> bytes:
    """The segments' bytes, concatenated in order: MPEG-TS segments of one
    stream concatenate into one playable stream."""
    if not keys:
        return b""
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        return b"".join(pool.map(_read_bytes, keys))


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_playlist(text: str) -> list:
    """Segments of an HLS playlist: name, wall-clock start, duration."""
    segments, start, duration = [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            start = _parse_time(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(line.split(":", 1)[1].split(",")[0])
            except ValueError:
                duration = None
        elif line and not line.startswith("#"):
            segments.append(
                {
                    "name": line,
                    "start": start,
                    "duration_ms": round(duration * 1000) if duration else None,
                }
            )
            start, duration = None, None
    return segments


def _list_segment_objects(prefix: str) -> list:
    names, token = [], None
    while True:
        kwargs = {"Bucket": default_storage.bucket_name, "Prefix": f"{prefix}/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = _s3().list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if _SEGMENT_RE.match(name):
                names.append(name)
        if not page.get("IsTruncated"):
            return names
        token = page.get("NextContinuationToken")


def list_segments(recording_id) -> list:
    """Uploaded segments of a recording, in order, with wall-clock starts.

    The main playlist is uploaded only every few segments; the live playlist
    holds the last five. Their union covers every segment; a segment present
    in neither (rare) gets its start from its predecessor."""
    prefix = recording_prefix(recording_id)
    by_name = {}
    for playlist in (PLAYLIST_NAME, LIVE_PLAYLIST_NAME):
        for seg in parse_playlist(_read_object(f"{prefix}/{playlist}") or ""):
            if seg["start"] is not None:
                by_name[seg["name"]] = seg
    for name in _list_segment_objects(prefix):
        by_name.setdefault(name, {"name": name, "start": None, "duration_ms": None})

    def index(seg):
        match = _SEGMENT_RE.match(seg["name"])
        return int(match.group(1)) if match else -1

    ordered = sorted(by_name.values(), key=index)
    default_ms = settings.LINTO_CATCHUP_SEGMENT_DURATION_S * 1000
    result: list = []
    for seg in ordered:
        if seg["start"] is None:
            if not result:
                continue
            last = result[-1]
            seg["start"] = last["start"] + timedelta(milliseconds=last["duration_ms"])
        seg["duration_ms"] = seg["duration_ms"] or default_ms
        seg["key"] = f"{prefix}/{seg['name']}"
        result.append(seg)
    return result


def segments_between(recording_id, after: Optional[datetime], up_to: datetime) -> list:
    """The segments not sent yet, up to `up_to`: those whose midpoint is after
    `after` (the end of what Meet already holds) and before `up_to`."""
    result = []
    for seg in list_segments(recording_id):
        midpoint = seg["start"] + timedelta(milliseconds=seg["duration_ms"] / 2)
        if midpoint >= up_to:
            break
        if after is not None and midpoint <= after:
            continue
        result.append(seg)
    return result


def list_prefixes() -> list:
    """Recording ids that have catch-up objects in the bucket."""
    ids, token = [], None
    while True:
        kwargs = {
            "Bucket": default_storage.bucket_name,
            "Prefix": _root_prefix(),
            "Delimiter": "/",
        }
        if token:
            kwargs["ContinuationToken"] = token
        page = _s3().list_objects_v2(**kwargs)
        for common in page.get("CommonPrefixes", []):
            recording_id = recording_id_of(common["Prefix"])
            if recording_id:
                ids.append(recording_id)
        if not page.get("IsTruncated"):
            return ids
        token = page.get("NextContinuationToken")


def delete_segments(recording_id) -> int:
    """Delete every object of a recording's catch-up prefix."""
    prefix = f"{recording_prefix(recording_id)}/"
    deleted, token = 0, None
    while True:
        kwargs = {"Bucket": default_storage.bucket_name, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = _s3().list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            _s3().delete_objects(
                Bucket=default_storage.bucket_name, Delete={"Objects": keys}
            )
            deleted += len(keys)
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    if deleted:
        logger.info(
            "Catch-up segments deleted for %s (%d objects)", recording_id, deleted
        )
    return deleted


# ── Studio, through its standard API ───────────────────────────────────────


class StudioClient:
    """The Studio calls of the catch-up, with the backend credential the
    deferred transcription uses: the upload goes through the ``linto`` SDK
    (same ASR service and organization choice), the rest through the
    conversation routes."""

    def __init__(self):
        self.base = (settings.LINTO_STUDIO_BASE_URL or "").rstrip("/")

    @staticmethod
    def _token(refresh=False) -> str:
        token = None if refresh else cache.get(CREDENTIAL_CACHE_KEY)
        if token:
            return token
        # Local import: bot_transcription imports the recording mediator.
        from core.services.bot_transcription import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
            BotTranscriptionException,
            BotTranscriptionService,
        )

        try:
            token = BotTranscriptionService()._login()  # noqa: SLF001  # pylint: disable=protected-access
        except BotTranscriptionException as e:
            raise CatchupError(str(e)) from e
        cache.set(CREDENTIAL_CACHE_KEY, token, CREDENTIAL_CACHE_TTL)
        return token

    def _call(self, method, path, **kwargs) -> requests.Response:
        response = None
        for refresh in (False, True):
            headers = {"Authorization": f"Bearer {self._token(refresh)}"}
            try:
                response = requests.request(
                    method,
                    f"{self.base}/api{path}",
                    headers=headers,
                    timeout=STUDIO_TIMEOUT,
                    **kwargs,
                )
            except requests.RequestException as e:
                raise CatchupError(f"Studio unreachable: {e}") from e
            if response.status_code != 401:
                return response
        return response

    def _ok(self, method, path, **kwargs) -> requests.Response:
        response = self._call(method, path, **kwargs)
        if not 200 <= response.status_code < 300:
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            detail = str(
                (
                    body.get("error") or body.get("message")
                    if isinstance(body, dict)
                    else ""
                )
                or (response.text or "")[:200]
            ).strip()
            raise StudioHTTPError(
                f"Studio {method} {path.split('?')[0]}: HTTP {response.status_code}"
                + (f" {detail}" if detail else ""),
                response.status_code,
                detail,
            )
        return response

    def upload(self, audio: bytes, *, language: str, name: str) -> str:
        """Upload the audio for a transcription without diarization; returns
        the conversation id."""
        from linto import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
            LinTO,
        )

        async def _upload():
            linto = LinTO(auth_token=self._token(), base_url=self.base)
            return await linto.upload(
                file=audio,
                enable_diarization=False,
                number_of_speaker="0",
                language=language,
                enablePunctuation=True,
                name=name,
                members_right=0,
            )

        try:
            return async_to_sync(_upload)()
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise CatchupError(f"Studio upload failed: {e}") from e

    def conversation(self, conversation_id: str) -> dict:
        """The conversation: its transcription job and its turns."""
        return self._ok("GET", f"/conversations/{conversation_id}").json() or {}

    def first_turn(self, conversation_id: str) -> dict:
        """The conversation's first turn, as Studio holds it."""
        turns = self.conversation(conversation_id).get("text") or []
        if not turns:
            raise CatchupError("Studio conversation without turns")
        return dict(turns[0])

    def prepend(self, conversation_id: str, text: str, first: dict):
        """Put `text` at the head of the conversation: its first turn (`first`,
        read before) takes the text, its own words kept (the export drops a
        turn without timing), and goes back right after it. Replaying it with
        the same `first` after a failure does not duplicate anything."""
        path = f"/conversations/{conversation_id}/turns/{first['turn_id']}"
        self._ok("PATCH", path, json={**first, "segment": text})
        original = {k: v for k, v in first.items() if k != "turn_id"}
        self._ok("POST", path, json=original)

    def trigger_summary(self, conversation_id: str, service_route: str):
        """Ask the LLM Gateway service for the summary of the conversation."""
        self._ok(
            "POST",
            f"/conversations/{conversation_id}/download",
            params={"format": service_route},
        )

    def summary(self, conversation_id: str, service_route: str):
        """(status, text) of the conversation's summary by `service_route`:
        status is ``running``, ``done`` or ``error``."""
        exports = self._ok(
            "GET", f"/conversations/{conversation_id}/export/list"
        ).json()
        export = next(
            (
                e
                for e in (exports if isinstance(exports, list) else [])
                if e.get("format") == service_route
            ),
            None,
        )
        if export is None:
            return "running", None
        status = export.get("status")
        if status == "error":
            return "error", str(export.get("error") or "").strip() or None
        if status not in ("complete", "done"):
            return "running", None
        job_id = export.get("jobId")
        if not job_id:
            return "done", _summary_text(export)
        content = self._ok(
            "GET", f"/conversations/{conversation_id}/export/{job_id}/content"
        ).json()
        return "done", _summary_text(content)

    def delete(self, conversation_id: str):
        """Delete the conversation and its audio in Studio."""
        response = self._call("DELETE", f"/conversations/{conversation_id}")
        if response.status_code not in (200, 204, 404):
            raise CatchupError(f"Studio delete: HTTP {response.status_code}")


def _summary_text(content) -> str:
    if isinstance(content, dict):
        content = content.get("content") or content.get("output") or ""
    return str(content or "").strip()


def _turns_text(conversation: dict) -> str:
    return "\n".join(
        (turn.get("segment") or "").strip()
        for turn in conversation.get("text") or []
        if (turn.get("segment") or "").strip()
    )


# ── State, one entry per recording in Meet's cache ─────────────────────────


def _state_key(recording_id) -> str:
    return f"{STATE_PREFIX}{recording_id}"


def _lock_key(recording_id, name: str) -> str:
    return f"linto_catchup:lock:{name}:{recording_id}"


def load_state(recording_id) -> dict:
    """What Meet holds for a recording: the text sent so far and the end of the
    audio it covers, the last brief, the running job and the last outcome."""
    return cache.get(_state_key(recording_id)) or {
        "transcript": "",
        "covered_until": None,
        "brief": None,
        "job": None,
        "outcome": None,
    }


def save_state(recording_id, state: dict):
    """Keep the state of a recording for the length of a meeting."""
    cache.set(_state_key(recording_id), state, STATE_TTL)


def discard_state(recording_id):
    """Delete a running job's conversation in Studio and the state."""
    job = (cache.get(_state_key(recording_id)) or {}).get("job")
    if job and job.get("conversation_id"):
        try:
            StudioClient().delete(job["conversation_id"])
        except CatchupError as e:
            logger.warning(
                "Catch-up conversation %s not deleted: %s", job["conversation_id"], e
            )
    cache.delete(_state_key(recording_id))


def _state_recording_ids() -> list:
    keys = getattr(cache, "keys", None)
    if keys is None:
        return []
    return [key[len(STATE_PREFIX) :] for key in keys(f"{STATE_PREFIX}*")]


@contextmanager
def _locked(recording_id, name: str, ttl: int):
    key = _lock_key(recording_id, name)
    acquired = cache.add(key, 1, ttl)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(key)


# ── Late joiner: the only moment anything is processed ─────────────────────


@async_to_sync
async def _participant_joined_at_ms(room_name: str, identity: str) -> Optional[int]:
    lkapi = utils.create_livekit_client()
    try:
        participant = await lkapi.room.get_participant(
            livekit_api.RoomParticipantIdentity(room=room_name, identity=identity)
        )
    except livekit_api.TwirpError as e:
        if e.code == "not_found":
            return None
        raise CatchupError(f"LiveKit get_participant failed: {e.message}") from e
    finally:
        await lkapi.aclose()
    return participant.joined_at_ms or (participant.joined_at * 1000) or None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _from_ms(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_recording(room) -> Optional[models.Recording]:
    return (
        models.Recording.objects.filter(
            room=room, status=models.RecordingStatusChoices.ACTIVE
        )
        .order_by("-created_at")
        .first()
    )


def _eta_rtf() -> tuple:
    try:
        low, high = (float(v) for v in settings.LINTO_CATCHUP_ETA_RTF[:2])
    except (TypeError, ValueError):
        low, high = 0.05, 0.3
    return low, max(low, high)


def _estimate(job: dict) -> tuple:
    """(progress 0..1, [min, max] seconds left) of a running job."""
    now = _now()
    if job["step"] == "summarizing":
        elapsed = (now - _parse_time(job["summary_started_at"])).total_seconds()
        low = max(0, SUMMARY_ETA_S[0] - elapsed)
        high = max(low, SUMMARY_ETA_S[1] - elapsed)
        return 0.85, [round(low), round(high)]
    elapsed = (now - _parse_time(job["started_at"])).total_seconds()
    low_rtf, high_rtf = _eta_rtf()
    audio_s = job.get("audio_s") or 0
    low = max(0, audio_s * low_rtf - elapsed) + SUMMARY_ETA_S[0]
    high = max(low, audio_s * high_rtf - elapsed + SUMMARY_ETA_S[1])
    expected = max(1.0, audio_s * high_rtf)
    return 0.05 + 0.75 * min(1.0, elapsed / expected), [round(low), round(high)]


def _view(recording, state: dict, joined_at=None) -> dict:
    """What the browser shows: the state, the progress of a running job, and
    the last brief with the end of the meeting it covers."""
    body = {"recording_id": str(recording.id)}
    if joined_at is not None:
        body["joined_at"] = _iso(joined_at)
    job, brief = state.get("job"), state.get("brief")
    if job:
        body["state"] = job["step"]
        body["progress"], body["eta_s"] = _estimate(job)
        body["up_to"] = job["up_to"]
    elif cache.get(_lock_key(recording.id, "start")) is not None:
        body["state"] = "sending"
    elif brief:
        body["state"] = "ready"
    else:
        body["state"] = state.get("outcome") or "none"
    if brief and not job:
        body["brief"] = {"text": brief["text"], "covered_until": brief["covered_until"]}
    return body


def _start_job(recording, state: dict, up_to: datetime):
    """Send Studio the minutes not sent yet, up to `up_to`."""
    segments = segments_between(
        recording.id, _parse_time(state.get("covered_until")), up_to
    )
    if not segments:
        if not state.get("brief"):
            state["outcome"] = "too_short"
        return
    audio = read_segments([seg["key"] for seg in segments])
    end = segments[-1]["start"] + timedelta(milliseconds=segments[-1]["duration_ms"])
    audio_s = sum(seg["duration_ms"] for seg in segments) / 1000
    language = (recording.options or {}).get("language") or "*"
    conversation_id = StudioClient().upload(
        audio, language=language, name=f"Visio catch-up {recording.id}"
    )
    state["job"] = {
        "conversation_id": conversation_id,
        "from": _iso(segments[0]["start"]),
        "up_to": _iso(end),
        "step": "transcribing",
        "started_at": _iso(_now()),
        "audio_s": audio_s,
        "errors": 0,
    }
    state["outcome"] = None
    logger.info(
        "Catch-up job for recording %s: %d segments (%.0f s, %d bytes) sent, "
        "conversation %s",
        recording.id,
        len(segments),
        audio_s,
        len(audio),
        conversation_id,
    )


def _end_job(state: dict, outcome: str):
    job = state.get("job") or {}
    if job.get("conversation_id"):
        try:
            StudioClient().delete(job["conversation_id"])
        except CatchupError as e:
            logger.warning(
                "Catch-up conversation %s not deleted: %s", job["conversation_id"], e
            )
    state["job"] = None
    state["outcome"] = outcome


def summary_unavailable() -> Optional[str]:
    """Why the summary service failed lately, or None."""
    return cache.get(SUMMARY_UNAVAILABLE_KEY)


def _mark_summary_unavailable(recording, reason: str):
    route = settings.LINTO_CATCHUP_SUMMARY_SERVICE
    cache.set(SUMMARY_UNAVAILABLE_KEY, f"{route}: {reason}", SUMMARY_RETRY_S)
    logger.warning(
        "Catch-up brief of %s failed: Studio could not run the LLM Gateway "
        "summary service %r (%s). Is it created on the gateway of %s "
        "(UPGRADE.md)? Late joiners get no brief for the next %d s.",
        recording.id,
        route,
        reason,
        settings.LINTO_STUDIO_BASE_URL,
        SUMMARY_RETRY_S,
    )


def _take_text(recording, state: dict, client, conversation: dict) -> bool:
    """Once per job, append the new text to what Meet holds and put what it
    held before at the head of the conversation. Every part is recorded in the
    job, so a step replayed after a failure never adds anything twice. False
    when too little was said to summarize (the job then ended)."""
    job = state["job"]
    if not job.get("taken"):
        previous = state.get("transcript") or ""
        job["held_chars"] = len(previous)
        state["transcript"] = "\n".join(
            t for t in (previous, _turns_text(conversation)) if t
        )
        state["covered_until"] = job["up_to"]
        job["taken"] = True
    if len(state["transcript"]) < settings.LINTO_CATCHUP_MIN_CHARS:
        _end_job(state, "ready" if state.get("brief") else "too_short")
        return False
    held = state["transcript"][: job.get("held_chars", 0)]
    if held and not job.get("head_put"):
        if not job.get("first_turn"):
            job["first_turn"] = client.first_turn(job["conversation_id"])
        client.prepend(job["conversation_id"], held, job["first_turn"])
        job["head_put"] = True
        logger.info(
            "Catch-up job of %s: %d characters held before put at the head",
            recording.id,
            len(held),
        )
    return True


def _advance(recording, state: dict):  # noqa: PLR0911  # pylint: disable=too-many-return-statements
    """One step of the running job: at most a few short Studio calls."""
    job = state["job"]
    if (_now() - _parse_time(job["started_at"])).total_seconds() > (
        settings.LINTO_CATCHUP_JOB_TIMEOUT_S
    ):
        logger.warning("Catch-up job of %s timed out", recording.id)
        _end_job(state, "failed")
        return
    client = StudioClient()
    conversation_id = job["conversation_id"]
    route = settings.LINTO_CATCHUP_SUMMARY_SERVICE
    try:
        if job["step"] == "transcribing":
            conversation = client.conversation(conversation_id)
            transcription = (conversation.get("jobs") or {}).get("transcription") or {}
            if transcription.get("state") == "error":
                logger.warning("Catch-up transcription of %s failed", recording.id)
                _end_job(state, "failed")
                return
            if transcription.get("state") != "done":
                return
            if not _take_text(recording, state, client, conversation):
                return
            try:
                client.trigger_summary(conversation_id, route)
            except StudioHTTPError as e:
                # The service is missing, or the gateway cannot run it: asking
                # again on this conversation would not help.
                _mark_summary_unavailable(recording, f"HTTP {e.status} {e.detail}")
                _end_job(state, "ready" if state.get("brief") else "failed")
                return
            job["step"] = "summarizing"
            job["summary_started_at"] = _iso(_now())
        elif job["step"] == "summarizing":
            status, text = client.summary(conversation_id, route)
            if status == "running":
                return
            if status == "error":
                _mark_summary_unavailable(recording, f"the job failed: {text}")
                _end_job(state, "ready" if state.get("brief") else "failed")
                return
            if not text:
                logger.warning(
                    "Catch-up summary of %s failed: the summary is empty", recording.id
                )
                _end_job(state, "ready" if state.get("brief") else "failed")
                return
            state["brief"] = {
                "text": text,
                "covered_until": job["up_to"],
                "generated_at": _iso(_now()),
            }
            _end_job(state, "ready")
            logger.info("Catch-up brief of %s ready", recording.id)
    except CatchupError as e:
        job["errors"] = job.get("errors", 0) + 1
        logger.warning(
            "Catch-up step of %s failed (%s): %s", recording.id, job["errors"], e
        )
        if job["errors"] >= MAX_STEP_ERRORS:
            _end_job(state, "failed")


def request_brief(  # noqa: PLR0911  # pylint: disable=too-many-return-statements
    room, identity: str, *, client_joined_at_ms=None
) -> dict:
    """A participant asks for the brief of what was said before they joined,
    once per arrival. Returns a dict for the browser."""
    if not is_enabled():
        return {"state": "unavailable", "reason": "disabled"}
    if live_run_active(room):
        return {"state": "live"}
    recording = _active_recording(room)
    if recording is None or not is_eligible(recording):
        return {"state": "none"}

    room_name = str(room.id)
    capture = find_capture(room_name, recording.id)
    if capture is None:
        return {
            "state": "unavailable",
            "reason": "no_capture",
            "recording_id": str(recording.id),
        }
    capture_started = (
        _from_ms(capture.started_at // 1_000_000) if capture.started_at else None
    )

    server_joined_ms = _participant_joined_at_ms(room_name, identity)
    if server_joined_ms is None:
        raise NotInRoomError("participant not in the room")
    joined_ms = server_joined_ms
    if client_joined_at_ms:
        # The client can only ask for less: a reload resets LiveKit's join time.
        joined_ms = min(joined_ms, int(client_joined_at_ms))
    joined_at = _from_ms(joined_ms)

    threshold = timedelta(seconds=settings.LINTO_CATCHUP_LATE_THRESHOLD_S)
    if capture_started is None or joined_at <= capture_started + threshold:
        return {"state": "none", "recording_id": str(recording.id)}

    with _locked(recording.id, "start", START_LOCK_TTL) as acquired:
        if not acquired:
            # Another arrival is sending the audio: poll for its job.
            return _view(recording, load_state(recording.id), joined_at)
        state = load_state(recording.id)
        brief = state.get("brief")
        reuse = timedelta(seconds=settings.LINTO_CATCHUP_REUSE_S)
        covered = _parse_time(brief["covered_until"]) if brief else None
        if not state.get("job") and not (covered and covered >= joined_at - reuse):
            unavailable = summary_unavailable()
            if unavailable:
                logger.info(
                    "Catch-up brief of %s not started, the summary service "
                    "failed lately (%s)",
                    recording.id,
                    unavailable,
                )
                return {
                    "state": "unavailable",
                    "reason": "summary_service",
                    "recording_id": str(recording.id),
                }
            _start_job(recording, state, joined_at)
            save_state(recording.id, state)
    return _view(recording, load_state(recording.id), joined_at)


def request_status(room, recording_id: str) -> dict:
    """The brief of a recording for a participant of the room: advances the
    running job by one step (one poll at a time), then answers its state."""
    recording = models.Recording.objects.filter(id=recording_id, room=room).first()
    if recording is None:
        return {"state": "none"}
    if load_state(recording.id).get("job"):
        with _locked(recording.id, "step", STEP_LOCK_TTL) as acquired:
            if acquired:
                state = load_state(recording.id)
                if state.get("job"):
                    _advance(recording, state)
                    save_state(recording.id, state)
    return _view(recording, load_state(recording.id))
