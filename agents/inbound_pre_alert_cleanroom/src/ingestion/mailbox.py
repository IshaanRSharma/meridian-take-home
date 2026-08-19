"""The mailbox, behind a capability key.

Generated code says ``email.fetch`` and never learns that it is Gmail. This is
the one file allowed to know, and it is not generated: swapping Gmail for
anything else is a change here plus a registry row, with nothing to alter in the
workflow, the checks or the action.

Two provider actions sit behind fetching mail, because listing messages and
downloading an attachment are separate calls at every provider, while the spec's
``capabilities`` names only ``email.fetch``. That shortfall is recorded as an
assumption rather than papered over.

The send path translates a neutral request into the provider's own argument
names, which is what keeps ``recipient_email`` out of the Action's file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from meridian.runtime.errors import BindingError, RetryableError

FETCH = "GMAIL_FETCH_EMAILS"
ATTACHMENT = "GMAIL_GET_ATTACHMENT"
SEND = "GMAIL_SEND_EMAIL"

# Pinned rather than "latest", which manual execution refuses outright. The same
# spec against a different toolkit version is a different build, exactly as the
# same spec against a different model is, so the pin belongs in the manifest.
TOOLKIT_VERSION = "20260817_00"

DOWNLOAD_TIMEOUT = 60.0


class GmailProvider:
    """One customer's mailbox, reached through their Composio connection.

    The credential is not here and never reaches the spec, the bindings or
    generated code: Composio holds the OAuth grant keyed by a user id, and this
    passes that id and nothing else.
    """

    def __init__(self, client: Any, user_id: str) -> None:
        """Take an authenticated, version-pinned client and the user id."""
        self._client = client
        self._user_id = user_id

    def execute(self, action: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        """Perform one provider action and unwrap the envelope.

        Composio reports a failed action by returning ``successful: False``
        rather than by raising, so catching only exceptions would read an error
        as an empty-but-fine result, which is how a check passes for the wrong
        reason.
        """
        if action == SEND:
            args = _as_gmail_send(args)
        data = self._call(action, args)
        if action == ATTACHMENT:
            return {"content": self._download(data)}
        return data

    def _call(self, action: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise BindingError(
                "the mailbox is not connected: no authenticated client was supplied. "
                "A live run needs one; a run over already-read documents does not."
            )
        try:
            envelope: Mapping[str, Any] = self._client.tools.execute(
                action, user_id=self._user_id, arguments=dict(args)
            )
        except Exception as error:
            raise RetryableError(f"{action} failed: {error}") from error
        if not envelope.get("successful", False):
            raise RetryableError(f"{action} failed: {envelope.get('error') or 'no reason given'}")
        return dict(envelope.get("data") or {})

    @staticmethod
    def _download(data: Mapping[str, Any]) -> bytes:
        """Fetch the bytes the provider parked, rather than the link to them."""
        url = (data.get("file") or {}).get("s3url")
        if not url:
            raise RetryableError("the attachment came back with no downloadable content")
        try:
            answer = httpx.get(str(url), timeout=DOWNLOAD_TIMEOUT)
            answer.raise_for_status()
        except httpx.HTTPError as error:
            raise RetryableError(f"attachment download failed: {error}") from error
        return answer.content


def _as_gmail_send(args: Mapping[str, Any]) -> dict[str, Any]:
    """A neutral notify request, in the provider's own argument names.

    The Action builds ``to``/``subject``/``body`` because those are the words
    the card uses. Which key a provider wants for a recipient is an integration
    detail, and keeping it here is what lets the Action file survive a change of
    mail provider.
    """
    recipients = args.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    first, *rest = list(recipients) or [""]
    return {
        "recipient_email": first,
        "extra_recipients": rest,
        "subject": args.get("subject", ""),
        "body": args.get("body", ""),
        "is_html": False,
    }


def composio_client() -> Any:
    """A version-pinned client for this deployment, or ``None`` when unconfigured.

    ``None`` rather than an exception: a run over already-read documents needs no
    mailbox at all, and refusing to construct anything would make the offline
    path depend on a key it never uses.
    """
    key = os.environ.get("COMPOSIO_API_KEY")
    if not key:
        return None
    from composio import Composio  # noqa: PLC0415 - kept out of the workflow's import graph

    return Composio(api_key=key, toolkit_versions={"gmail": TOOLKIT_VERSION})
