"""The bodies of the core MCP tool wrappers, kept out of the registrar."""

from typing import Any, NamedTuple

from handlers import generate_prepare, jira, jira_stage_file, wizard


def tool_with_doc(mcp: Any, doc: str, **kwargs: Any) -> Any:
    """Decorator: set ``fn.__doc__`` to ``doc``, then register it on ``mcp``."""

    def _attach(fn: Any) -> Any:
        fn.__doc__ = doc
        return mcp.tool(**kwargs)(fn)

    return _attach


class PrepareToolArgs(NamedTuple):
    """The nine arguments qa_generate_test_cases and qa_prepare_test_cases share."""

    feature_or_url: str
    proceed_anyway: bool
    jira_content_json: str
    stage_token: str
    source_plan: str
    attached_image_count: int
    capture_ids: list[str] | None
    image_gate_ack: bool
    image_carry_ack: bool

    @classmethod
    def from_locals(cls, loc: dict[str, Any]) -> "PrepareToolArgs":
        """Pick the nine fields by name; ``loc`` also holds ``rt`` and ``ctx``."""
        return cls(*(loc[name] for name in cls._fields))

    def params(self) -> dict[str, Any]:
        """The old ``_params`` dict, key for key."""
        return {
            "feature_or_url": self.feature_or_url,
            "proceed_anyway": self.proceed_anyway,
            "jira_content_json": self.jira_content_json,
            "stage_token": self.stage_token,
            "source_plan": self.source_plan,
            "attached_image_count": self.attached_image_count,
            "capture_ids": list(self.capture_ids or []),
            "image_gate_ack": self.image_gate_ack,
            "image_carry_ack": self.image_carry_ack,
        }


async def generate_call(rt: Any, loc: dict[str, Any]) -> str:
    """Run qa_generate_test_cases, replaying an identical recent call."""
    _params = PrepareToolArgs.from_locals(loc).params()
    _cache_key = rt._generate_cache_key(_params)
    _cached = rt._recent_call_cached(_cache_key, rt._GENERATE_DUP_WINDOW_S)
    if _cached is not None:
        return rt._DUP_REPLY_PREFIX + _cached
    _result = await rt._tracked(
        "qa_generate_test_cases",
        loc["ctx"],
        rt._prepare_call(wizard.handle_generate_test_cases, loc["ctx"], _params),
    )
    # Only a reply that created a prep: replaying a clarify or a gate
    # block would hide a changed tester answer (e.g. the P0-3 dialog).
    if "**prep_id:** `" in _result:
        rt._recent_call_store(_cache_key, _result)
    return _result


def _prepare_kwargs(rt: Any, ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
    return dict(
        proceed_anyway=args["proceed_anyway"],
        **rt._make_elicitors(ctx),
        progress=rt._make_progress(ctx),
        jira_content_json=args["jira_content_json"],
        stage_token=args["stage_token"],
        source_plan=args["source_plan"],
        attached_image_count=args["attached_image_count"],
        capture_ids=args["capture_ids"],
        image_gate_ack=args["image_gate_ack"],
        image_carry_ack=args["image_carry_ack"],
    )


async def prepare_call(rt: Any, loc: dict[str, Any]) -> Any:
    """Run qa_prepare_test_cases and shape the payload into content blocks."""
    ctx = loc["ctx"]
    args = PrepareToolArgs.from_locals(loc).params()
    result = await rt._tracked(
        "qa_prepare_test_cases",
        ctx,
        generate_prepare.handle_prepare_test_cases(
            args["feature_or_url"], **_prepare_kwargs(rt, ctx, args)
        ),
    )
    return rt._prepare_payload_to_content(result)


async def stage_call(rt: Any, loc: dict[str, Any]) -> str:
    """Run qa_stage_jira."""
    if not loc.get("path") and not loc.get("json"):
        return "Give `json` (the stringified fetch result) or `path` (the absolute path of a file holding it)."
    if loc.get("path") and not loc.get("json"):
        work = jira_stage_file.handle_stage_jira_file(
            loc["stage_token"], loc["part"], loc["path"]
        )
    else:
        work = jira.handle_stage_jira(loc["stage_token"], loc["part"], loc["json"])
    return await rt._tracked("qa_stage_jira", loc["ctx"], work)
