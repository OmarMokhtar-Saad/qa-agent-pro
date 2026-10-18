"""The pinned identity of the QA input-method APK. DATA ONLY -- no imports.

Phase 0 Part B of the mobile programme, cut 2026-09-04. Every value here is a
claim about an artifact that lives OUTSIDE this repository, which is why this
module could not be written before the release existed: a placeholder hash is
worse than an absent one, because it looks like a pin.

The claim was measured, not copied. The published asset was downloaded and
hashed, and the digest below is what those bytes produce -- it also matches the
``.sha256`` sidecar published beside it. Note that an EARLIER run of the same
workflow reported a different digest (``b82e96fe...``): a re-run rebuilds the
APK with a new ``versionCode`` and fresh signing timestamps, so the bytes
differ. Pinning the number a build log printed, rather than the number the
served asset produces, is exactly how a manifest ends up describing an artifact
nobody can download.

``tools/mobile/ime.py`` resolves this module BY NAME at call time, so its mere
presence is what turns the lane on: the registration gate in
``mcp_handlers._mobile_lane_enabled``, the ``qa-doctor`` line, the per-run
preflight and ``scripts/build_dist.py``'s mobile-file exclusion all consult the
same absence and start passing together.
"""

#: Release this pin describes. Matches the tag `qa-ime-v1.2.2`.
IME_VERSION = "1.2.2"

#: applicationId / namespace from mobile/qa-ime/build.gradle.kts.
IME_PACKAGE = "io.qaagents.ime"

#: The InputMethodService, as declared in AndroidManifest.xml (`.QaImeService`).
IME_SERVICE = "io.qaagents.ime.QaImeService"

#: The published release asset. HTTPS, and the only URL the downloader accepts.
IME_ASSET_URL = (
    "https://github.com/OmarMokhtar-Saad/qa-agent-pro/releases/download/"
    "qa-ime-v1.2.2/qa-ime-1.2.2.apk"
)

#: SHA-256 of the 655146 bytes served at IME_ASSET_URL, verified 2026-10-18 against
#: the asset itself; same signer as 1.2.0 and 1.2.1. 1.2.0 was the first build signed
#: with a NEW key (cert SHA-256 b2c15076...; the 1.1.0 key is lost), so a device holding
#: 1.1.0 refuses the update and ime_fresh.install_pinned replaces OUR package once in the
#: same run; imes_upgrade.refused_note's uninstall line shows only if that also fails.
IME_SHA256 = "01276dc9092858e5904ed8fa0293a771bf3f63ce30f2a52eb73d8e77c5f97ef3"

#: The text-field broadcasts QaImeService registers in onCreate (QaImeService.kt:72-113; it also
#: registers ARM/DISARM/DUMP, which this module does not state).
#: `ime.manifest()` defaults these from IME_PACKAGE; they are stated rather than
#: inferred so a future package rename cannot silently change the wire protocol.
ACTION_INPUT = "io.qaagents.ime.INPUT"
ACTION_CLEAR = "io.qaagents.ime.CLEAR"
ACTION_QUERY = "io.qaagents.ime.QUERY"
