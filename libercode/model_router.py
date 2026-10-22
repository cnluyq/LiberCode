"""
Model routing for LiberCode.

Resolves which LLM model should serve a given agent or task at runtime,
based on the ``model_router`` section of ``libercode.json``. Deterministic
rules (explicit override / keyword / role / default) are evaluated without
extra LLM calls; an optional LLM classifier can be configured for tasks
that match no deterministic rule.

All routed models must be available on the currently connected provider
(same ``LLM_API_KEY`` / ``LLM_BASE_URL`` — only the ``model`` argument of
the API call differs).
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def _as_str_list(value: Any) -> list:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, int, float))]


class ModelRouter:
    """Deterministic model resolver driven by ``libercode.json`` ``model_router``.

    Resolution order for a task/teammate:

    1. explicit model (tool argument / task field)
    2. runtime role override (``/route role``)
    3. ``by_keyword`` first match on the task text (subject + description /
       spawn prompt)
    4. ``by_role`` match on the teammate role
    5. runtime default override (``/route default``)
    6. ``default``
    7. the global model (``MODEL_ID`` / ``/model``)

    :meth:`resolve` returns ``(model_id, matched)`` — ``matched`` is False
    when only the global fallback applied. Callers should pin the model on
    the agent/task only when ``matched`` is True (or after a successful
    classifier hit), so un-routed agents keep following global ``/model``
    switches.
    """

    def __init__(
        self,
        raw_config: Any,
        get_global_model: Optional[Callable[[], str]] = None,
        is_listed: Optional[Callable[[str], bool]] = None,
    ):
        self.raw = raw_config if isinstance(raw_config, dict) else {}
        self._get_global_model = get_global_model or (lambda: "")
        # is_listed(model_id) -> False flags a model as known-unavailable on
        # the connected provider (used to annotate describe() output)
        self._is_listed = is_listed or (lambda model_id: True)
        # Runtime overrides (set via the /route REPL command, persisted with
        # sessions). Lead/default are scalars; role maps role -> model.
        self._lead_override: Optional[str] = None
        self._default_override: Optional[str] = None
        self._role_overrides: dict = {}

    # -- basic accessors ---------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    @property
    def global_model(self) -> str:
        return self._get_global_model() or ""

    def lead_rule(self) -> Optional[str]:
        """The effective ``lead`` rule (dynamic override, else config), or None.

        Returns None when routing is disabled — the lead then follows the
        global model.
        """
        if not self.enabled:
            return None
        if self._lead_override:
            return self._lead_override
        lead = self.raw.get("lead")
        return str(lead) if lead else None

    # -- runtime overrides (/route command) --------------------------------

    def set_override(self, kind: str, key: Optional[str] = None, model: Optional[str] = None) -> None:
        """Set a runtime override.

        Args:
            kind: One of "lead", "default", "role" ("agent" is handled by
                TeammateManager, not here)
            key: Role name (for kind="role")
            model: Model id; None clears the override
        """
        if kind == "lead":
            self._lead_override = model or None
        elif kind == "default":
            self._default_override = model or None
        elif kind == "role" and key:
            if model:
                self._role_overrides[str(key).strip().lower()] = str(model)
            else:
                self._role_overrides.pop(str(key).strip().lower(), None)

    def reset_overrides(self) -> None:
        """Clear all runtime overrides (back to libercode.json rules)."""
        self._lead_override = None
        self._default_override = None
        self._role_overrides = {}

    def get_overrides(self) -> dict:
        """Serializable snapshot of runtime overrides (for session persistence)."""
        return {
            "lead": self._lead_override,
            "default": self._default_override,
            "role": dict(self._role_overrides),
        }

    def apply_overrides(self, overrides: Any) -> None:
        """Restore runtime overrides from a persisted snapshot."""
        self.reset_overrides()
        if not isinstance(overrides, dict):
            return
        lead = overrides.get("lead")
        default = overrides.get("default")
        role = overrides.get("role")
        if lead:
            self._lead_override = str(lead)
        if default:
            self._default_override = str(default)
        if isinstance(role, dict):
            self._role_overrides = {
                str(k).strip().lower(): str(v)
                for k, v in role.items()
                if v
            }

    # -- rule parsing ------------------------------------------------------

    def _keyword_rules(self) -> list:
        rules = self.raw.get("by_keyword")
        if not isinstance(rules, list):
            return []
        parsed = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            model = rule.get("model")
            keywords = [
                k.lower()
                for k in _as_str_list(rule.get("keywords", rule.get("patterns", [])))
            ]
            if model and keywords:
                parsed.append({"model": str(model), "keywords": keywords})
        return parsed

    def _role_map(self) -> dict:
        by_role = self.raw.get("by_role")
        return (
            {str(k).strip().lower(): str(v) for k, v in by_role.items()}
            if isinstance(by_role, dict)
            else {}
        )

    def resolve(self, role: str = "", text: str = "", explicit: Optional[str] = None) -> tuple:
        """Resolve a model deterministically.

        Args:
            role: Teammate role (matched against ``by_role`` and runtime
                role overrides)
            text: Task description / spawn prompt (matched against ``by_keyword``)
            explicit: Explicit model id override (highest priority)

        Returns:
            Tuple of ``(model_id, matched)`` where ``matched`` indicates a
            routing rule (explicit/keyword/role/default) produced the model
        """
        if explicit:
            return str(explicit), True
        if not self.enabled:
            return self.global_model, False
        role_key = str(role).strip().lower()
        if role_key and role_key in self._role_overrides:
            return self._role_overrides[role_key], True
        lowered = (text or "").lower()
        for rule in self._keyword_rules():
            if any(kw in lowered for kw in rule["keywords"]):
                return rule["model"], True
        if role_key:
            model = self._role_map().get(role_key)
            if model:
                return model, True
        if self._default_override:
            return self._default_override, True
        if self.raw.get("default"):
            return str(self.raw["default"]), True
        return self.global_model, False

    def configured_models(self) -> list:
        """All model ids referenced by the router config (sorted, unique).

        Includes runtime overrides. Used for availability diagnostics
        against the provider model list.
        """
        models: set = set()
        for key in ("lead", "default"):
            value = self.raw.get(key)
            if value:
                models.add(str(value))
        models.update(self._role_map().values())
        models.update(rule["model"] for rule in self._keyword_rules())
        cfg = self.classifier()
        if cfg:
            models.add(cfg["model"])
            models.update(cat["model"] for cat in cfg["categories"])
        if self._lead_override:
            models.add(self._lead_override)
        if self._default_override:
            models.add(self._default_override)
        models.update(self._role_overrides.values())
        return sorted(models)

    # -- optional LLM classifier -------------------------------------------

    def classifier(self) -> Optional[dict]:
        """Return the parsed classifier config, or None if not configured.

        The config is ``{"model": ..., "categories": [{"name", "model",
        "description"}, ...]}``.
        """
        cfg = self.raw.get("classifier")
        if not isinstance(cfg, dict):
            return None
        model = cfg.get("model")
        categories = cfg.get("categories")
        if not model or not isinstance(categories, list):
            return None
        parsed = []
        for cat in categories:
            if isinstance(cat, dict) and cat.get("name") and cat.get("model"):
                parsed.append(
                    {
                        "name": str(cat["name"]),
                        "model": str(cat["model"]),
                        "description": str(cat.get("description", "")),
                    }
                )
        return {"model": str(model), "categories": parsed} if parsed else None

    def classify_prompt(self, role: str, text: str) -> Optional[tuple]:
        """Build the (model, prompt) pair for LLM classification.

        Returns:
            Tuple of (classifier_model_id, prompt), or None when no
            classifier is configured
        """
        cfg = self.classifier()
        if not cfg:
            return None
        lines = [
            "Classify the following task into exactly one category.",
            "Reply with ONLY the category name, nothing else.",
            "",
            "Categories:",
        ]
        for cat in cfg["categories"]:
            desc = f" - {cat['description']}" if cat["description"] else ""
            lines.append(f"- {cat['name']}{desc}")
        lines.append("")
        if role:
            lines.append(f"Required role: {role}")
        lines.append(f"Task: {(text or '').strip()[:2000]}")
        lines.append("")
        lines.append("Category:")
        return cfg["model"], "\n".join(lines)

    def category_model(self, category_name: str) -> Optional[str]:
        """Map a classifier reply to a model id via fuzzy category match."""
        cfg = self.classifier()
        if not cfg:
            return None
        name = (category_name or "").strip().lower()
        if not name:
            return None
        for cat in cfg["categories"]:
            cat_name = cat["name"].lower()
            if cat_name == name or cat_name in name or name in cat_name:
                return cat["model"]
        return None

    # -- display -----------------------------------------------------------

    def _show(self, model_id: str) -> str:
        """Format a model id, flagging models the provider lacks."""
        if not self._is_listed(model_id):
            return f"{model_id} [UNAVAILABLE]"
        return model_id

    def describe(self) -> str:
        """Human-readable routing summary (for ``/model routes``)."""
        lines = [f"Model routing: {'enabled' if self.enabled else 'disabled'}"]
        if not self.enabled:
            return "\n".join(lines)
        if self._lead_override:
            lines.append(
                f"  lead: {self._show(self._lead_override)} (dynamic override)"
            )
        else:
            lead = self.raw.get("lead")
            lines.append(f"  lead: {self._show(str(lead)) if lead else '(global)'}")
        if self._default_override:
            lines.append(
                f"  default: {self._show(self._default_override)} (dynamic override)"
            )
        else:
            default = self.raw.get("default")
            lines.append(f"  default: {self._show(str(default)) if default else '(global)'}")
        by_role = self._role_map()
        override_roles = set(self._role_overrides)
        roles = sorted(set(by_role) | override_roles)
        if roles:
            lines.append("  by_role:")
            for role in roles:
                if role in self._role_overrides:
                    lines.append(
                        f"    {role} -> {self._show(self._role_overrides[role])} (dynamic override)"
                    )
                else:
                    lines.append(f"    {role} -> {self._show(by_role[role])}")
        rules = self._keyword_rules()
        if rules:
            lines.append("  by_keyword:")
            for rule in rules:
                lines.append(f"    {','.join(rule['keywords'])} -> {self._show(rule['model'])}")
        cfg = self.classifier()
        if cfg:
            lines.append(f"  classifier: {self._show(cfg['model'])}")
            for cat in cfg["categories"]:
                lines.append(f"    {cat['name']} -> {self._show(cat['model'])}")
        return "\n".join(lines)
