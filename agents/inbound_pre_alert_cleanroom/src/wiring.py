"""What every capability resolves to here, and who fills every role.

The only file that knows a provider's name or reads an environment variable.
Generated code says ``email.send``; this says that is Gmail, or that it is a
recorder. Swapping one for the other is a flag rather than a fork, which is what
keeps the demo path and the eval path the same code.

**Recording is the default.** The graded artefact is the row the process
returns, so scoring a case needs no I/O at all — and a recorded call is better
evidence than a delivered one, because *this action was invoked once, with these
batch numbers* is a test expectation while a delivered message is a screenshot.

**The registry carries a capability the spec does not.** ``capabilities`` names
``email.fetch`` and ``email.send``; reading a pre-alert also needs the
attachments, which is a second provider action everywhere. It is declared here
under its own key rather than smuggled behind ``email.fetch``, so that the gap
is visible in one place instead of being invisible in all of them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from ingestion.mailbox import ATTACHMENT, FETCH, SEND, GmailProvider, composio_client

from meridian.runtime.tools import RecordingProvider, Tool, Tools

FETCH_CAPABILITY = "email.fetch"
ATTACHMENT_CAPABILITY = "email.attachment"
SEND_CAPABILITY = "email.send"

MAILBOX = "mailbox"
MAILER = "mailer"

REGISTRY: Mapping[str, Tool] = {
    FETCH_CAPABILITY: Tool(provider=MAILBOX, action=FETCH),
    ATTACHMENT_CAPABILITY: Tool(provider=MAILBOX, action=ATTACHMENT),
    SEND_CAPABILITY: Tool(provider=MAILER, action=SEND),
}

ROLE_PREFIX = "MERIDIAN_ROLE_"

# What a role resolves to when nothing binds it. Deliberately not an address:
# there is no bindings file in this repository, and inventing a plausible
# mailbox would mean a live run mails a stranger. A run still produces its row,
# and a live send fails at the provider rather than succeeding at the wrong
# person.
UNBOUND = "unbound:{role}"


def tools(*, live_send: bool = False) -> Tools:
    """The toolbox for this run.

    Args:
        live_send: whether the notify capability actually sends. Off everywhere
            except a demo, because a suite that mails a supervisor once per case
            is a suite nobody can run twice.

    Returns:
        Capabilities resolved to providers, with nothing else able to reach out.
    """
    client = composio_client()
    user = os.environ.get("COMPOSIO_ENTITY_ID", "")
    mailbox = GmailProvider(client, user)
    mailer = mailbox if live_send else RecordingProvider()
    return Tools(REGISTRY, {MAILBOX: mailbox, MAILER: mailer})


def roles(*names: str) -> dict[str, str]:
    """Who fills each role for this customer, from the environment.

    Identities must never enter the checksummed spec: the same spec deploys to a
    second customer with different people, and a personnel change must not force
    a new spec version. In production this table is a committed YAML file; here
    it is the environment, and an unbound role is named as unbound rather than
    guessed at.
    """
    return {
        name: os.environ.get(f"{ROLE_PREFIX}{name.upper()}") or UNBOUND.format(role=name)
        for name in names
    }
