"""Application settings, backed by pydantic-settings (QW-14 / I-045 / B-017).

House rule: the settings modules (this one and ``config/settings_*.py``) import
**nothing internal except each other**. All configuration comes
from the environment / ``.env`` only. Every field is validated with a lenient,
never-raising coercer so a malformed value (e.g. ``QA_RAG_TOP_K=abc``) degrades
to its documented default with a logged warning instead of crashing the whole
app at import time.

Field names map 1:1 to their upper-case environment variables (case-insensitive):
``qa_export_dir`` <- ``QA_EXPORT_DIR``, ``jira_base_url`` <- ``JIRA_BASE_URL``,
and so on.
"""

from __future__ import annotations

from dotenv import load_dotenv
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.settings_api import ApiPushSettings
from config.settings_capture import CaptureSettings
from config.settings_coerce import (
    BOOL_FIELDS,
    coerce_bool,
)
from config.settings_coerce import logger as logger
from config.settings_host import HostSettings
from config.settings_ints import (
    JIRA_INT_FIELDS,
    coerce_jira_int,
    coerce_top_k,
)
from config.settings_jira import JiraSettings
from config.settings_loader import load, offending_fields
from config.settings_rag import RagSettings
from config.settings_reconcile import RECONCILE_FIELDS, coerce_reconcile_threshold
from config.settings_runtime import RuntimeSettings
from config.settings_scores import (
    CHECKLIST_FLOAT_FIELDS,
    coerce_checklist_float,
)
from config.settings_store import StoreSettings

# Load .env into os.environ so libraries that read the environment directly
# also see the values -- the anthropic SDK's ANTHROPIC_API_KEY is the live
# example. (This comment named langsmith / langgraph until 2026-08-15, when
# dead-code deletion Phase 2 batch P2-A deleted graph.py and dropped both
# packages; the call itself is unaffected.) BaseSettings additionally reads the
# same file for its own declared fields below.
load_dotenv()


class Settings(
    RuntimeSettings,
    HostSettings,
    StoreSettings,
    RagSettings,
    ApiPushSettings,
    CaptureSettings,
    JiraSettings,
    BaseSettings,
):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator(*BOOL_FIELDS, mode="before")
    @classmethod
    def _coerce_bool(cls, v: object, info) -> bool:
        return coerce_bool(v, info.field_name, cls.model_fields)

    @field_validator(*CHECKLIST_FLOAT_FIELDS, mode="before")
    @classmethod
    def _coerce_checklist_float(cls, v: object, info) -> float:
        return coerce_checklist_float(v, info.field_name, cls.model_fields)

    @field_validator(*RECONCILE_FIELDS, mode="before")
    @classmethod
    def _coerce_reconcile_threshold(cls, v: object, info) -> float:
        return coerce_reconcile_threshold(v, info.field_name, cls.model_fields)

    @field_validator("qa_rag_top_k", mode="before")
    @classmethod
    def _coerce_top_k(cls, v: object) -> int:
        return coerce_top_k(v)

    @field_validator(*JIRA_INT_FIELDS, mode="before")
    @classmethod
    def _coerce_jira_int(cls, v: object, info) -> int:
        return coerce_jira_int(v, info.field_name, cls.model_fields)


def _offending_fields(exc: ValidationError) -> list[str]:
    """Names of the Settings fields pydantic rejected. Never raises."""
    return offending_fields(exc, Settings)


def _load_settings() -> Settings:
    """Build Settings, degrading field by field (see ``settings_loader.load``).

    ``Settings`` is looked up here at call time so tests can monkeypatch it.
    """
    return load(Settings)


settings = _load_settings()
