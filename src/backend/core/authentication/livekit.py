"""Authentication using LiveKit token for the Meet core app."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.crypto import constant_time_compare

from livekit.api import TokenVerifier
from rest_framework import authentication, exceptions

from core.utils import user_id_proof

UserModel = get_user_model()


class LiveKitTokenAuthentication(authentication.BaseAuthentication):
    """Authenticate using LiveKit token and load the associated Django user."""

    def authenticate(self, request):
        # A standard "Authorization: Bearer <token>" header is accepted; POSTs may
        # instead carry the room token in the body, and GET actions (e.g. the LinTO
        # bot-status/bot-profiles) can only pass it as a query param.
        auth_header = request.headers.get("Authorization")
        if auth_header:
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise exceptions.AuthenticationFailed(
                    "Authorization header must be: Bearer <token>"
                )
            token = parts[1]
        else:
            token = request.data.get("token") or request.query_params.get("token")

        if not token:
            return None  # No authentication attempted

        try:
            verifier = TokenVerifier(
                api_key=settings.LIVEKIT_CONFIGURATION["api_key"],
                api_secret=settings.LIVEKIT_CONFIGURATION["api_secret"],
            )
            claims = verifier.verify(token)

            user_id = claims.identity
            if not user_id:
                raise exceptions.AuthenticationFailed("Token missing user identity")

            return (self._user_of(claims), claims)

        except Exception as e:
            raise exceptions.AuthenticationFailed(
                f"Invalid LiveKit token: {str(e)}"
            ) from e

    @staticmethod
    def _user_of(claims):
        """The user of the token: by its `user_id` attribute, which counts only
        with the proof Meet set beside it. The identity alone names nobody: an
        anonymous participant's comes from the client."""
        attributes = getattr(claims, "attributes", None) or {}
        pk = attributes.get("user_id")
        room = claims.video.room if claims.video else ""
        if pk and constant_time_compare(
            attributes.get("user_id_proof", ""),
            user_id_proof(room, claims.identity, pk),
        ):
            try:
                return UserModel.objects.get(pk=pk, is_active=True)
            except (UserModel.DoesNotExist, ValueError, DjangoValidationError):
                pass
        return AnonymousUser()
