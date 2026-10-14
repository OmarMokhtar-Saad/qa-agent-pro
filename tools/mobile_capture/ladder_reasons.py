"""The refusal vocabulary of the capture ladder: reason codes and sentences.

Split out of :mod:`tools.mobile_capture.ladder`, which re-exports every name
here, so ``ladder.REASON_*`` / ``ladder.REASONS`` /
``ladder._REASON_SENTENCES`` keep resolving.
"""

from __future__ import annotations

from tools.mobile_shared.capture_tiers import REASON_PINNED

#: The refusal vocabulary. Every code below has exactly one sentence in
#: ``_REASON_SENTENCES``, proven by ``test_every_reason_code_has_a_sentence_``
#: ``and_no_two_share_one``.
REASON_NO_CONSENT = "no_consent"
REASON_NO_APPLY = "no_apply"
REASON_NO_MITMDUMP = "no_mitmdump"
REASON_CERT_NOT_TRUSTED = "cert_not_trusted"
REASON_TLS_FAILURE = "tls_failure"
REASON_PROXY_START_FAILED = "proxy_start_failed"
REASON_DEVICE_GONE = "device_gone"
REASON_USER_STORE_ONLY = "user_store_only"
REASON_INCONCLUSIVE = "inconclusive"
#: The lane kill-switch is unset, so the root CA was not installed. Same
#: string as ``cert.REASON_FLAG_OFF``, which ``prepare`` passes through.
REASON_FLAG_OFF = "flag_off"

#: Nobody has been asked about this device yet. NOT the same as
#: REASON_NO_CONSENT: a decline is an answer, and this is the absence of the
#: question. Collapsing the two would tell a tester they had refused something
#: they were never offered.
REASON_NOT_OFFERED = "not_offered"
REASONS = frozenset(
    {
        REASON_NO_CONSENT,
        REASON_NO_APPLY,
        REASON_NO_MITMDUMP,
        REASON_CERT_NOT_TRUSTED,
        REASON_PINNED,
        REASON_TLS_FAILURE,
        REASON_PROXY_START_FAILED,
        REASON_DEVICE_GONE,
        REASON_USER_STORE_ONLY,
        REASON_INCONCLUSIVE,
        REASON_NOT_OFFERED,
        REASON_FLAG_OFF,
    }
)

#: One sentence per reason code. THE ONE PRODUCER of the tester-facing
#: sentence for a refusal -- a rendered sentence can change without any
#: caller's branch changing, because callers branch on the CODE.
_REASON_SENTENCES = {
    REASON_NO_CONSENT: (
        "The tester declined API capture, so this run continues without it."
    ),
    REASON_FLAG_OFF: (
        "API capture installs a root certificate on the device, which needs "
        "`QA_MOBILE_HTTPS_CAPTURE_ENABLED=true` in `.env`; nothing was installed."
    ),
    REASON_NO_APPLY: (
        "API capture needs apply=true to install or start anything; nothing "
        "was touched."
    ),
    REASON_NO_MITMDUMP: (
        "mitmdump is not provisioned yet, so no proxy could be started."
    ),
    REASON_CERT_NOT_TRUSTED: (
        "The device did not accept the qa-agents root certificate."
    ),
    REASON_PINNED: (
        "The app pins its own certificates, so its requests fail once our "
        "certificate is installed."
    ),
    REASON_TLS_FAILURE: (
        "The proxy could not complete a TLS handshake through the device."
    ),
    REASON_PROXY_START_FAILED: (
        "The qa-agents proxy did not start, so this run continues without capture."
    ),
    REASON_DEVICE_GONE: ("The device disconnected before capture could be confirmed."),
    REASON_USER_STORE_ONLY: (
        "Only the user certificate store accepted the CA; most apps on this "
        "device ignore it."
    ),
    REASON_INCONCLUSIVE: (
        "Capture could not be confirmed one way or the other on this run."
    ),
    REASON_NOT_OFFERED: (
        "API capture is not set up on this device. Call again with "
        'capture="on" to record the app\'s API calls.'
    ),
}
