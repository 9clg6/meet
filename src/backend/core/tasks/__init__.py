"""Celery tasks for the core app."""

from core.tasks.connection_test import delete_connection_test_room
from core.tasks.file import process_file_deletion
from core.tasks.linto_catchup import (
    delete_linto_catchup_segments,
    start_linto_catchup_capture,
)
from core.tasks.mail import send_email

__all__ = (
    "delete_connection_test_room",
    "delete_linto_catchup_segments",
    "process_file_deletion",
    "send_email",
    "start_linto_catchup_capture",
)
