"""Tests for the LinTO recap delivery helpers of core.tasks.linto."""

# pylint: disable=protected-access,no-member

import contextlib
import smtplib
from datetime import datetime, timezone
from unittest import mock

from django.core import mail

import pytest
from asgiref.sync import async_to_sync

from core import factories
from core.tasks import linto as linto_task

pytestmark = pytest.mark.django_db

REPORT = "# Analytical Report - Test\n\n**Date:** 2026-09-24\n\n## Agenda\n\n1. Test"


class _MemoryCheckpoint(linto_task._StepCheckpoint):
    """A checkpoint over a plain dict, as the Recording/Room stores behave."""

    def __init__(self, steps=None):
        self.steps = dict(steps or {})

        async def _mark(name, value):
            self.steps[name] = value

        super().__init__(get=self.steps.get, mark=_mark)


class TestRecapEmail:
    """The recap email links the Drive folder and attaches the document."""

    def _send(self, **kwargs):
        user = factories.UserFactory(email="owner@example.com", language="en-us")
        params = {
            "recipient_users": [user],
            "room_name": "okr-mcwr-mqk",
            "meeting_dt": datetime(2026, 9, 24, 14, 32, tzinfo=timezone.utc),
            "with_summary": True,
            "twake_drive_link": "https://owner-drive.example.com/#/folder/dir-1",
            "pdf_content": None,
            "pub_filename": "summary.pdf",
            "pub_mime": "application/pdf",
            "log_id": "rec-1",
            "checkpoint": _MemoryCheckpoint(),
        }
        params.update(kwargs)
        async_to_sync(linto_task._send_recap_emails)(**params)
        assert len(mail.outbox) == 1
        return mail.outbox[0]

    def test_with_a_summary_the_email_announces_it(self):
        message = self._send(twake_drive_link=None, pdf_content=b"%PDF")
        for text in (message.body, message.alternatives[0][0]):
            assert "The transcription and summary of" in text
            assert "The summary document is attached" in text
            assert "The transcription document is attached" not in text

    def test_the_document_is_attached_next_to_the_drive_link(self):
        """The document in the Drive folder also comes attached."""
        message = self._send(pdf_content=b"DOCX", pub_filename="summary.docx")
        assert message.attachments[0][:2] == ("summary.docx", b"DOCX")
        for text in (message.body, message.alternatives[0][0]):
            assert "owner-drive.example.com" in text
            assert "The summary document is attached" in text

    def test_without_a_summary_the_email_announces_the_transcript_alone(self):
        """No summary (not entitled, or turned off): the email stands on the
        transcript — no summary promised, the attachment named for what it is."""
        message = self._send(
            with_summary=False,
            twake_drive_link=None,
            pdf_content=b"docx bytes",
            pub_filename="2026 09 24 - Transcript.docx",
            pub_mime="application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document",
        )
        for text in (message.body, message.alternatives[0][0]):
            assert "The transcription of" in text
            assert "is now available" in text
            assert "summary" not in text.lower()
            assert "The transcription document is attached to this email." in text
        assert message.attachments[0][0] == "2026 09 24 - Transcript.docx"

    def test_without_a_summary_the_drive_link_stays(self):
        """The Drive link does not depend on the summary."""
        message = self._send(with_summary=False)
        assert "The transcription of" in message.body
        assert "owner-drive.example.com" in message.body
        assert "summary" not in message.body.lower()


class TestSharedSummary:
    """The summary goes to the signed-in attendees, the recipient aside."""

    SUMMARY = {"success": True, "job_id": "job-1", "content": REPORT}

    def _share(self, attendee_ids, **kwargs):
        params = {
            "attendee_ids": attendee_ids,
            "recipient_user": None,
            "room_name": "okr-mcwr-mqk",
            "meeting_dt": datetime(2026, 9, 24, 14, 32, tzinfo=timezone.utc),
            "summary_result": self.SUMMARY,
            "pdf_content": b"DOCX",
            "pub_filename": "summary.docx",
            "pub_mime": "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document",
            "log_id": "rec-1",
            "checkpoint": _MemoryCheckpoint(),
        }
        params.update(kwargs)
        async_to_sync(linto_task._share_summary)(**params)
        return params["checkpoint"]

    def test_each_attendee_gets_the_document(self):
        """Every attendee gets the document and the preview, no Drive link."""
        alice = factories.UserFactory(email="alice@example.com", language="en-us")
        bob = factories.UserFactory(email="bob@example.com", language="en-us")
        checkpoint = self._share([str(alice.id), str(bob.id)])
        assert sorted(m.to[0] for m in mail.outbox) == [
            "alice@example.com",
            "bob@example.com",
        ]
        message = mail.outbox[0]
        assert message.subject == "A meeting summary has been shared with you"
        assert message.attachments[0][:2] == ("summary.docx", b"DOCX")
        for text in (message.body, message.alternatives[0][0]):
            assert "shares its summary with the participants" in text
            assert "The summary document is attached" in text
            assert "Analytical Report" not in text
            assert "Twake" not in text
        assert sorted(checkpoint.steps["shared"]) == sorted(
            [str(alice.id), str(bob.id)]
        )

    def test_the_recipient_and_users_without_email_are_left_out(self):
        """Neither the recipient nor a user without email or inactive gets it."""
        owner = factories.UserFactory(email="owner@example.com")
        no_email = factories.UserFactory(email=None)
        inactive = factories.UserFactory(email="gone@example.com", is_active=False)
        guest = factories.UserFactory(email="guest@example.com")
        self._share(
            [str(owner.id), str(no_email.id), str(inactive.id), str(guest.id)],
            recipient_user=owner,
        )
        assert [m.to for m in mail.outbox] == [["guest@example.com"]]

    def test_no_summary_nothing_is_shared(self):
        """A transcript alone is never shared."""
        guest = factories.UserFactory(email="guest@example.com")
        self._share([str(guest.id)], summary_result={})
        assert mail.outbox == []

    def test_a_resumed_share_skips_who_already_got_it(self):
        """A retry only emails the attendees not reached yet."""
        alice = factories.UserFactory(email="alice@example.com")
        bob = factories.UserFactory(email="bob@example.com")
        checkpoint = _MemoryCheckpoint({"shared": [str(alice.id)]})
        self._share([str(alice.id), str(bob.id)], checkpoint=checkpoint)
        assert [m.to for m in mail.outbox] == [["bob@example.com"]]
        assert checkpoint.steps["shared"] == [str(alice.id), str(bob.id)]

    def test_a_failed_send_is_retried_next_time(self):
        """An SMTP failure leaves the attendee unchecked."""
        alice = factories.UserFactory(email="alice@example.com")
        with mock.patch.object(
            linto_task.EmailMultiAlternatives,
            "send",
            side_effect=smtplib.SMTPException("down"),
        ):
            checkpoint = self._share([str(alice.id)])
        assert "shared" not in checkpoint.steps


class TestDocumentName:
    """The published document is named for what it holds."""

    def test_the_summary_report(self):
        recording = factories.RecordingFactory()
        name = linto_task._document_filename(
            recording, "pdf", "fr-fr", {"success": True, "job_id": "job-1"}
        )
        assert name.endswith(" - Résumé.pdf")

    def test_the_transcript_without_a_summary(self):
        recording = factories.RecordingFactory()
        assert linto_task._document_filename(recording, "docx", "fr-fr", {}).endswith(
            " - Transcription.docx"
        )
        assert linto_task._document_filename(
            recording, "docx", "en", {"success": False}
        ).endswith(" - Transcript.docx")


class TestTwakeLinkOnResume:
    """A task resumed after the Twake step still links the Drive folder."""

    def _deliver(self, checkpoint, settings, **owner):
        settings.CLOUDERY_URL = "http://cloudery.test"
        settings.CLOUDERY_TOKEN = "tok"
        settings.TWAKE_DEV_INSTANCE_OVERRIDE = None
        owner = factories.UserFactory(**{"sub": "owner-sub", **owner})
        recording = factories.RecordingFactory()
        with contextlib.ExitStack() as stack:
            token = stack.enter_context(
                mock.patch(
                    "core.services.twake_drive.get_drive_token",
                    mock.AsyncMock(return_value="drive-token"),
                )
            )
            stack.enter_context(
                mock.patch(
                    "core.services.twake_drive.ensure_meeting_directory",
                    mock.AsyncMock(return_value="dir-1"),
                )
            )
            stack.enter_context(
                mock.patch("core.services.twake_drive.save_file", mock.AsyncMock())
            )
            link = async_to_sync(linto_task._deliver_to_twake)(
                conversation_id="conv-1",
                recipient_user=owner,
                transcript_text="hello",
                pdf_content=None,
                pub_filename="summary.pdf",
                pub_mime="application/pdf",
                meeting=recording,
                language=None,
                extra_files_provider=None,
                log_id="rec-1",
                checkpoint=checkpoint,
            )
        return link, token

    def test_the_link_is_checkpointed(self, settings):
        """The upload stores the folder link with the step."""
        checkpoint = _MemoryCheckpoint()
        link, _ = self._deliver(checkpoint, settings)
        assert link.endswith("/#/folder/dir-1")
        assert checkpoint.steps["twake"] == link

    def test_an_email_sub_uploads_to_the_stored_instance(self, settings):
        """The token is asked for the instance stored at sign-in."""
        _, token = self._deliver(
            _MemoryCheckpoint(),
            settings,
            sub="bob@example.com",
            twake_instance="bob-1.twake.test",
        )
        assert token.await_args.kwargs["instance"] == "bob-1.twake.test"

    def test_an_email_sub_without_an_instance_skips_the_upload(self, settings):
        """No token request, no link, and the step stays open."""
        checkpoint = _MemoryCheckpoint()
        link, token = self._deliver(
            checkpoint, settings, sub="bob@example.com", twake_instance=None
        )
        assert link is None
        token.assert_not_awaited()
        assert "twake" not in checkpoint.steps

    def test_a_resume_returns_the_checkpointed_link(self, settings):
        """No second upload, and the email still gets the link."""
        stored = "https://owner-drive.example.com/#/folder/dir-1"
        link, token = self._deliver(_MemoryCheckpoint({"twake": stored}), settings)
        assert link == stored
        token.assert_not_awaited()

    def test_a_step_checkpointed_before_the_link_was_kept(self, settings):
        """An older state only knows the step is done: no link, no upload."""
        link, token = self._deliver(_MemoryCheckpoint({"twake": True}), settings)
        assert link is None
        token.assert_not_awaited()
