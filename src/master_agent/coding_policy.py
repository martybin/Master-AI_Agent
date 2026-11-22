"""Explicit owner choice wins; n8n remains the default builder."""

import re

WRITE_ACTION = (
    r"\b(build|create|write|fix|implement|refactor|complete|finish|edit|update|improve|modify|extend|repair|add)\b"
    r"|بساز|بنویس|پیاده|اصلاح|تغییر بده|قرار بده|بذار|بزار"
    r"|تکمیل|ویرایش|توسعه|درستش|ادیت|بهبود|کاملش|کامل کن|رفع|بازنویس|درست کن|آپدیت"
)


def coding_action(request):
    return bool(re.search(WRITE_ACTION + r"|\brun\b|اجرا", request.lower()))


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
        r"فقط توضیح|فقط بگو|کد ننویس|تغییر نده|(?:تکمیل|ویرایش|ادامه|اصلاح)\s+نکن"
        r"|explain only|do not (write|edit|change)|stop|cancel",
        latest.lower(),
    ):
        return None
    if re.search(r"اجرا\s+نکن|do not run", latest.lower()) and not re.search(
        WRITE_ACTION, latest.lower()
    ):
        return None
    short = bool(
        re.fullmatch(r"(?:بنویس|تکمیل کن|ادامه بده|انجام بده|بزن)[؟?! .]*", latest.strip())
    )
    if coding_action(latest) and not short:
        return latest
    if short or re.search(
        r"نوشتی|چی\s*شد|چیشد|ادامه|انجام بده|پوشه|فولدر|دسترسی|همین|continue|do it|what happened",
        latest.lower(),
    ):
        for i in range(len(messages) - 2, -1, -1):
            m = messages[i]
            if m["role"] == "user" and coding_action(m["content"]):
                replies = [r for r in messages[i + 1 :] if r["role"] == "assistant"]
                if replies and (
                    replies[-1].get("execution_complete")
                    or replies[-1].get("error_code") == "interrupted"
                ):
                    return None
                return m["content"]
        return latest if short else None
    return None


def missing_execution(request, observations):
    """A fluent reply or a read cannot count as implementing an explicit edit."""
    writes = bool(re.search(WRITE_ACTION, request.lower()))
    successful = [o for o in observations if "result" in o and not o.get("error")]
    required = (
        {"code.write_file", "code.replace", "project.create_python_agent"}
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
    if needs_tests and not re.search(
        r"بدون تست|بدون آزمایش|no tests|(?:تست|فرمان).{0,18}اجرا نکن|do not run tests",
        request.lower(),
    ):
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
