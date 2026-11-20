"""Tasks of the LinTO catch-up without live transcription (fork).

Only deferred, best-effort chores run here: the delayed start of a capture next
to a video recording, and the deletion of segments once a running upload has
read them.
The reconciler is a management command run by a CronJob, not a beat task: Meet's
solo worker can be held for an hour by a deferred transcription.
"""

import logging

from core import models
from core.services import linto_catchup
from core.tasks._task import task

logger = logging.getLogger(__name__)


@task
def start_linto_catchup_capture(recording_id: str):
    """Start the catch-up capture of a recording that is still active."""
    recording = (
        models.Recording.objects.select_related("room").filter(id=recording_id).first()
    )
    if recording is None:
        return
    try:
        linto_catchup.start_capture(recording)
    except linto_catchup.CatchupError:
        logger.exception("Catch-up capture start failed for %s", recording_id)


@task
def delete_linto_catchup_segments(recording_id: str):
    """Delete a recording's catch-up segments once its capture no longer runs.
    The recording itself may still read "active" for a while after its egress
    ended (until its file is seen in the storage): that does not keep them."""
    recording = models.Recording.objects.filter(id=recording_id).first()
    try:
        if recording is not None and linto_catchup.find_capture(
            str(recording.room_id), recording.id
        ):
            return
        linto_catchup.delete_segments(recording_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Catch-up segments deletion failed for %s", recording_id)
