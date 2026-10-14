"""Tester-facing rendering of a self-check report."""

from __future__ import annotations

from typing import Mapping


def _verdict_line(findings: list, floors: list) -> str:
    if findings:
        return (
            f"❌ **{len(findings)} finding(s).** A reply below names "
            "something this build cannot deliver, so a tester acting on it "
            "gets nothing."
        )
    if floors:
        return (
            "⚠️ **No findings, but the pass did not scan enough to "
            "mean it** — treat this as INCONCLUSIVE, not clean: " + ", ".join(floors)
        )
    return "✅ **No findings.**"


def _render_findings(findings: list) -> list:
    lines = ["", "### Findings"]
    for item in findings:
        lines.append(
            f"- **{item.get('kind')}** in `{item.get('surface')}`: "
            f"`{item.get('detail')}`"
        )
    return lines


def _render_egress(egress: list) -> list:
    return [
        "",
        "### Outbound connections blocked during the pass",
        "Nothing left this machine; each attempt was refused and recorded.",
    ] + [f"- `{item.get('surface')}` → `{item.get('host')}`" for item in egress]


def _render_counts(scanned: Mapping) -> list:
    return [
        "",
        f"- Text surfaces read: **{scanned.get('surfaces', 0)}** "
        f"({scanned.get('chars', 0)} characters)",
        "- Tool names checked against this edition's registry: "
        f"**{scanned.get('tool_mentions', 0)}**",
        f"- Settings/credential names checked: **{scanned.get('flag_tokens', 0)}**",
        f"- Module names checked: **{scanned.get('module_tokens', 0)}**; model "
        f"references found (any is a finding): **{scanned.get('model_tokens', 0)}**",
    ]


def _render_skips(skips: list) -> list:
    return ["", f"### Not checked ({len(skips)})"] + [
        f"- `{item.get('surface')}` — {item.get('reason')}" for item in skips
    ]


_FOOTER = (
    "_Every handler was called with its own declared defaults, so no "
    "acknowledgement and no `apply=true` was ever sent and no external "
    "write was possible._"
)


def render(report: Mapping) -> str:
    """The tester-facing reply. Markdown, and honest about what it did not do."""
    scanned = report.get("scanned") or {}
    findings = list(report.get("findings") or ())
    skips = list(report.get("skips") or ())
    floors = list(report.get("floors") or ())
    egress = list(report.get("egress") or ())
    lines = ["## Self-check — what this install's replies actually say", ""]
    lines.append(_verdict_line(findings, floors))
    lines += _render_counts(scanned)
    if findings:
        lines += _render_findings(findings)
    if skips:
        lines += _render_skips(skips)
    if egress:
        lines += _render_egress(egress)
    lines += ["", _FOOTER]
    return "\n".join(lines)
