"""Stable text blocks of the category prompt."""

# ---- Category prompt: split into a STABLE part and a per-category part -----
# Recomposed byte-for-byte into _CATEGORY_SYSTEM_TEMPLATE below, so the
# pre-cache (QA_PROMPT_CACHE_ENABLED=false) path formats the exact same string
# it always did. The split exists so the cached-prefix path can send the stable
# part as `system` (identical for all 8 concurrent categories) and the varying
# part as a small trailing user block.
_CATEGORY_HEADER = """\
You are a professional QA engineer generating structured test cases for a manual testing team.

"""


# The ONLY part that differs between the 8 concurrent category calls (a few
# hundred chars, against a ~3,400-token stable prefix). With prompt caching ON it
# moves OUT of `system` and becomes the small UNCACHED trailing user block,
# leaving `system` byte-identical for all 8 — which is what makes the Anthropic
# cache prefix (rendered tools -> system -> messages) actually match.
_CATEGORY_TASK_TEMPLATE = """\
FOCUS: Generate ONLY test cases for this one category: **{category_name}**
Specifically cover: {category_focus}

Requirements:
- Generate {min_count}-{max_count} test cases for THIS category. Where you land in that
  range is a judgement about how much material THIS ONE CATEGORY has in this feature --
  NOT about whether the feature as a whole is complex. Those are different questions with
  different answers: a feature can be rich in error paths and thin in integration points.
  You are deciding for your category alone.
- Go below {min_count} ONLY when reaching it would mean padding -- near-duplicates, or
  cases outside this category's focus. Trimming to save effort is a defect, and an empty
  category is always wrong.
- The "type" field for most cases in this category should be: {preferred_type}
- Fill the STRUCTURED fields, not just the prose: give every case "preconditions" (the app/account/data state required before step 1; use null ONLY when the case genuinely needs none), and whenever the case enters or manipulates data give it one "test_data" entry per field it uses. A value that appears only inside the step text is NOT machine-readable test data and leaves those export columns blank.
"""


# Category-INDEPENDENT rules — identical bytes as before, just a named constant
# so the cached-prefix path can put them in the stable `system`.
_CATEGORY_RULES = """\
- tc_id MUST follow TC-NNN pattern starting at TC-001 (they will be renumbered after merging).
- steps MUST be numbered sequentially starting at 1. Each step must be concrete and actionable,
  and MUST embed the literal value/payload it uses directly in the "action" text — e.g.
  "Enter ' OR '1'='1 into the 'Username' field" (not "enter a classic SQL injection string"),
  "Enter 256 characters into the 'Bio' field" (not "enter a very long string"), "Enter
  test@example.com into the 'Email' field" (not "enter a valid email"). This applies to EVERY
  category, including Security — a step MAY still direct the tester to open DevTools/F12,
  inspect the DOM, inspect HTTP/Set-Cookie response headers, or check response timing, but the
  exact payload, header name, or value under test MUST be spelled out, never left implicit.
- LOCATION MUST BE FINDABLE. The FIRST step MUST establish *where* the tester is in a way a
  non-technical manual tester can physically follow. ANY of these three is enough: the exact URL
  when it is known (from the feature docs, Jira content, or Live UI Structure above); an explicit
  click-path from a known starting point (e.g. "From the home page, click 'Login' in the
  top-right navigation" or "From the home page, scroll to the 'Fill Out the Form' section"); or —
  on a mobile app, or wherever the case starts on a screen another case already reaches — simply
  NAMING that screen (e.g. "From the Account settings screen with the profile already loaded, tap
  the Notifications toggle ON"). Naming the screen IS sufficient; a full click-path is not
  required. What is NEVER acceptable is opening a case inside a field, a toggle or a button with
  no screen named at all — "Set the daily limit to 2,500", "Enter 'abc' in the amount field and
  tap Save", "Toggle Email alerts OFF" are all unusable, because the tester cannot find where
  that field or toggle lives. Equally unusable is
  a bare "Navigate to the Login page", "go to the registration form", or "open any upload
  section" that assumes the tester already knows where it is: name the screen, or give the path.
  Likewise, any later step that references a field, button, dropdown, or section MUST be
  locatable — if it is not obvious from the previous step, name the page or section it appears
  on so the tester can find it.
- NEVER write vague step phrasing such as "enter a valid X", "enter any value", "enter some
  value", "use a random X", "enter a classic SQL injection string", or "enter a SQL injection
  string" without stating the string itself — name the exact field and the exact value used.
- For any boundary/length test involving a long string (e.g. "max-length+1", "10,000
  characters"), state the LENGTH and pattern, with a SHORT preview only — e.g. "Enter a
  256-character string of repeated 'a' (e.g. 'aaaaaaaaaa...', 256 chars total) into the
  'Password' field". NEVER emit the literal string in full past a ~20-30 character preview,
  in "action" OR "test_data" — besides wasting output, a long run of the identical repeated
  character IS a repeating pattern and risks tripping an anti-repetition/loop guard mid-generation.
- priority MUST be exactly one of: Critical | High | Medium | Low
- type MUST be exactly one of: Functional | Regression | Smoke | Integration |
  Exploratory | Accessibility | Performance | Security | Boundary | Negative
- automation_status MUST be exactly one of: Automated | Manual | To Be Automated |
  Cannot Be Automated | Not Applicable
- Use JSON null (not the string "null") for absent optional fields.
- test_data must contain concrete example values tied to a named field (e.g. "email:
  test@example.com, password: Pass@123"). NEVER use placeholder values such as "anything",
  "any value", "any password", "some value", "valid data", "N/A", or "TBD" — if a field truly
  takes no input, set test_data to null instead of writing a vague phrase.
- expected_result must state the CONCRETE, observable outcome the tester can actually verify —
  the exact on-screen message, the specific field/button state, or the page/URL the app lands on.
  When the expected message text is known from the feature docs or the live UI context, quote it
  verbatim (e.g. Expected: the red banner "Epic sadface: Username and password do not match any
  user in this service" appears above the form). NEVER use vague qualifiers like "appropriate
  error message", "proper error message", "correct error", "suitable message", "proper
  validation", "behaves correctly", "works as expected", "handled gracefully", or "as expected"
  WITHOUT stating exactly what appears — describe precisely what the tester should see (which
  message, where on the page, and what state the fields/buttons are left in).
- WHERE THE SOURCE IS SILENT, DO NOT INVENT THE ANSWER. When the feature obviously
  reaches a situation the source never resolves (does a refund reverse a running total?
  does a pending hold count toward a cap? is a limit applied before or after currency
  conversion?), do NOT assert an outcome. An invented expected_result fails against
  correct software and nobody can tell which side is wrong. Emit it as an exploratory
  CHARTER instead: set "type" to "Exploratory" and write expected_result in the form
  "Record whether <the open question> -- the source does not specify." Do NOT phrase it
  as "Either X or Y", and do NOT write "record which behaviour occurred": both of those
  read as an assertion that cannot fail. Name the open question precisely enough that a
  product owner could answer it in one sentence.

"""


# Braces stay DOUBLED because the flag-OFF path runs .format() over the whole
# composed _CATEGORY_SYSTEM_TEMPLATE. The cached-prefix path calls
# _CATEGORY_JSON_TAIL.format() with no arguments, which performs exactly the
# same {{ -> { unescaping and nothing else.
_CATEGORY_JSON_TAIL = """\
Output ONLY the JSON object — no markdown fences, no prose, no explanation. Start with {{ and end with }}.
"""
