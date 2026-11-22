"""Explicit owner choice wins; n8n remains the default builder."""

import re


def coding_action(request):
    return bool(
        re.search(
            r"\b(build|create|write|fix|implement|refactor|run)\b"
            r"|بساز|بنویس|پیاده|اصلاح|اجرا|تغییر بده|قرار بده|بذار|بزار",
            request.lower(),
        )
    )


def target_for(chat, request):
    selected = chat.get("target", "auto")
    if selected != "auto":
        return selected
    text = request.lower().replace("\u200c", " ")
    if re.search(r"\.py\b|encoder|decoder|انکود|دیکود", text) and "n8n" not in text:
        return "code"
    python = bool(re.search(r"\bpython\b|\.py\b|پایتون", text))
    if python and "n8n" not in text:
        return "python"
    if "n8n" in text:
        return "n8n"
    if any(word in text for word in ("کدنویسی", "کد نویسی", "coding", "debug", "دیباگ")):
        return "code"
    for message in reversed(chat.get("messages", [])):
        if message.get("role") == "user" and message.get("content") != request:
            previous = target_for({}, message["content"])
            if previous != "n8n":
                return previous
            if "n8n" in message["content"].lower():
                break
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


def execution_request(messages):
    """Short continuations retain the last user-authored coding task."""
    latest = messages[-1]["content"]
    if re.search(
        r"فقط توضیح|فقط بگو|کد ننویس|تغییر نده|explain only|do not (write|edit|change)",
        latest.lower(),
    ):
        return None
    if coding_action(latest):
        return latest
    if re.search(r"نوشتی|ادامه|انجام بده|قرار بده|بذار|بزار|continue|do it", latest.lower()):
        return next(
            (
                m["content"]
                for m in reversed(messages)
                if m["role"] == "user" and coding_action(m["content"])
            ),
            latest,
        )
    return None


def missing_execution(request, observations):
    """A fluent reply or a read cannot count as implementing an explicit edit."""
    writes = bool(
        re.search(
            r"write|create|build|implement|fix|refactor|بنویس|بساز|پیاده|اصلاح|قرار بده|بذار|بزار",
            request.lower(),
        )
    )
    successful = [o for o in observations if "result" in o and not o.get("error")]
    required = (
        {"code.write_file", "code.replace", "project.create_python_agent", "terminal.exec"}
        if writes
        else {"terminal.exec", "code.check", "project.check"}
    )
    performed = any(
        o["tool"] in required
        and not (
            isinstance(o["result"], dict)
            and (
                o["result"].get("exit_code", 0) != 0
                or o["result"].get("timed_out")
                or o["result"].get("run", {}).get("status") == "failed"
            )
        )
        for o in successful
    )

    if not performed:
        return True
    needs_tests = bool(re.search(r"\btests\b|\bunittest\b|تست|آزمایش", request.lower()))
    if needs_tests and not re.search(r"بدون تست|بدون آزمایش|no tests", request.lower()):
        last_write = max(
            (
                i
                for i, o in enumerate(successful)
                if o["tool"] in {"code.write_file", "code.replace", "project.create_python_agent"}
            ),
            default=-1,
        )
        successful = successful[last_write + 1 :]
        checks = [o for o in successful if o["tool"] in {"code.check", "project.check"}]
        if not checks:
            terminal = [o["result"] for o in successful if o["tool"] == "terminal.exec"]
            return not any(
                r.get("exit_code") == 0
                and not r.get("timed_out")
                and re.search(r"Ran [1-9]\d* tests?", r.get("stdout", "") + r.get("stderr", ""))
                and re.search(r"(?:^|\n)OK(?:\s|$)", r.get("stdout", "") + r.get("stderr", ""))
                for r in terminal
            )
        run = checks[-1]["result"].get("run", {})
        return run.get("status") != "passed" or run.get("tests_run", 0) < 1
    return False
