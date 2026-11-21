"""Concrete integration prerequisites and read-only tools for chat progress tracking."""

import re

READ_ONLY_TOOLS = frozenset(
    {
        "system.inspect",
        "connections.list",
        "catalog.search",
        "catalog.describe",
        "files.list",
        "files.read",
        "n8n.workflows",
        "n8n.inspect_workflow",
        "n8n.executions",
    }
)


def personal_birthday_request(request: str) -> bool:
    text = request.lower().replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return (
        any(word in text for word in ("تلگرام", "telegram"))
        and any(word in text for word in ("تولد", "birthday"))
        and any(word in text for word in ("کانتکت", "مخاطب", "contact"))
        and any(
            word in text
            for word in (
                "اتوماسیون",
                "تبریک",
                "بفرست",
                "ارسال",
                "طراحی",
                "بساز",
                "خودکار",
                "automation",
                "greeting",
                "send",
                "workflow",
                "design",
            )
        )
    )


def connection_guidance(request: str, connections: list[dict]) -> str | None:
    """A real missing prerequisite ends inference; an empty list is not progress."""
    if not personal_birthday_request(request):
        # A status/file task may legitimately consume an empty registry. Only a
        # Telegram integration request requires configuring one before proceeding.
        if not connections and any(word in request.lower() for word in ("تلگرام", "telegram")):
            return (
                "هنوز هیچ اتصالی ثبت نشده است. از «Local Agent و اتصال‌ها» یک بات، "
                "اکانت شخصی تلگرام یا سرویس موردنیازت را اضافه کن. برای تلگرام، "
                "توکن یا اطلاعات ورود را فقط در همان پنل وارد کن؛ بعد طراحی را ادامه می‌دهیم."
            )
        return None

    personal = [c for c in connections if c.get("kind") == "telegram_user"]
    setup = None
    if not personal:
        setup = (
            "برای مخاطبان حساب خودت، اتصال «اکانت شخصی تلگرام» لازم است؛ بات به "
            "فهرست مخاطبان حساب شخصی دسترسی ندارد. در «Local Agent و اتصال‌ها» "
            "اکانت شخصی را اضافه کن و ورودش را از همان پنل کامل کن."
        )
    elif not any(c.get("status") == "connected" for c in personal):
        setup = (
            "اتصال اکانت شخصی ثبت شده، اما ورودش کامل نشده است. در «Local Agent و "
            "اتصال‌ها»، «ورود به حساب» و سپس «بررسی اتصال» را انجام بده."
        )
    # A named file or explicit Telegram birthday source can be inspected in the
    # next planning phase. Never interpret arbitrary chat dates as a contact DB.
    source_known = bool(re.search(r"\.(csv|json|xlsx)\b", request, re.IGNORECASE)) or any(
        phrase in request.lower()
        for phrase in ("تولدهای قابل", "پروفایل تلگرام", "telegram birthdays")
    )
    if not setup and source_known:
        return None
    parts = [
        "طرح پیشنهادی: بررسی روزانهٔ تاریخ تولدها → انتخاب مخاطبان امروز → "
        "کنترل ارسال تکراری برای هر مخاطب در هر سال → ارسال تبریک و ثبت نتیجه.",
    ]
    if setup:
        parts.append(setup)
    if not source_known:
        parts.append(
            "تاریخ تولد مخاطب‌ها را در فایل CSV/JSON می‌دهی یا می‌خواهی از تولدهای "
            "قابل‌مشاهدهٔ پروفایل تلگرام استفاده شود؟ تولد مخفی یا ثبت‌نشده را "
            "نمی‌توان برای همهٔ مخاطبان خودکار به دست آورد."
        )
    parts.append("این طرح اولیه است؛ ساخت و فعال‌سازی Workflow اجرایی مرحلهٔ بعد است.")
    return "\n\n".join(parts)
