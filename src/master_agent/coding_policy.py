"""Explicit owner choice wins; n8n remains the default builder."""

import re


def coding_action(request):
    return bool(
        re.search(
            r"\b(build|create|write|fix|implement|refactor|run)\b"
            r"|بساز|بنویس|پیاده|اصلاح|اجرا|تغییر بده",
            request.lower(),
        )
    )


def target_for(chat, request):
    selected = chat.get("target", "auto")
    if selected != "auto":
        return selected
    text = request.lower().replace("\u200c", " ")
    python = bool(re.search(r"\bpython\b|\.py\b|پایتون", text))
    if python and "n8n" not in text:
        return "python"
    if "n8n" in text:
        return "n8n"
    if any(word in text for word in ("کدنویسی", "کد نویسی", "coding", "debug", "دیباگ")):
        return "code"
    return "n8n"


def decision_schema(manifest, *, force_tool=False):
    """Bind each tool name to its own argument shape during constrained decoding."""
    import copy

    variants, definitions = [], {}
    for index, entry in enumerate(manifest):
        parameters = copy.deepcopy(entry["parameters"])
        local_defs = parameters.pop("$defs", {})
        prefix = f"tool_{index}_"

        def rebase(item, prefix=prefix):
            if isinstance(item, dict):
                return {
                    key: "#/$defs/" + prefix + value.removeprefix("#/$defs/")
                    if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/")
                    else rebase(value, prefix)
                    for key, value in item.items()
                }
            if isinstance(item, list):
                return [rebase(value, prefix) for value in item]
            return item

        definitions.update({prefix + key: rebase(value) for key, value in local_defs.items()})
        variants.append(
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool"},
                    "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "tool_name": {"const": entry["name"]},
                    "tool_arguments": rebase(parameters),
                },
                "required": ["action", "message", "tool_name", "tool_arguments"],
                "additionalProperties": False,
            }
        )
    if not force_tool:
        variants.append(
            {
                "type": "object",
                "properties": {
                    "action": {"const": "reply"},
                    "message": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "tool_name": {"type": "null"},
                    "tool_arguments": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                "required": ["action", "message", "tool_name", "tool_arguments"],
                "additionalProperties": False,
            }
        )
    result = {"title": "ChatDecision", "type": "object", "anyOf": variants}
    if definitions:
        result["$defs"] = definitions
    return result
