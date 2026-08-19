"""When a batch number on an invoice and one on a certificate are the same batch.

The criterion is ``each_has_matching``, which compares exactly — and exact is
what the spec's own negative statement insists on: *"if the letters, digits,
order, or case differ in any other way, it is a different batch and must be
reported."* Exactly one difference is settled knowledge and therefore allowed:

    [rule] A batch number on the invoice and the certificate counts as the same
    batch when the only difference is a trailing lot suffix letter that is
    omitted on the certificate.

That is a business rule the process owner approved, not normalisation invented
here — which is the distinction that decides whether tolerance belongs in a
matcher at all. It is written as a standalone predicate so a repair that finds
the corpus needs a different tolerance changes one function, and so the tolerance
that *was* approved stays readable beside the ones that were not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

Matcher = Callable[[str, str], bool]
"""``(listed_on_invoice, printed_on_certificate) -> same batch``.

Directional on purpose: the suffix is dropped by the certificate, so the
tolerance is not symmetric and a symmetric matcher would accept a certificate
carrying a suffix the invoice does not — a case nobody settled.
"""


def same_batch(listed: str, certified: str) -> bool:
    """Whether a certificate's batch number denotes the batch the invoice listed."""
    if listed == certified:
        return True
    # The invoice's trailing lot letter is the only thing a certificate may omit;
    # everything else differing makes it a different batch.
    return len(listed) > 1 and listed[-1].isalpha() and listed[:-1] == certified


def has_match(listed: str, certified: Iterable[str], matcher: Matcher = same_batch) -> bool:
    """Whether any certificate on hand carries the batch this invoice line listed.

    Duplicates on the certificate side are harmless — the spec says any one of
    several certificates for a batch may be used, because they are the same
    document — so this asks only whether one exists.
    """
    return any(matcher(listed, one) for one in certified)
