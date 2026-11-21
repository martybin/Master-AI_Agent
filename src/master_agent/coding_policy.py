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
