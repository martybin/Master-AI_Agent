export const statuses = {
  draft: "پیش‌نویس",
  deploying: "در حال ارسال",
  deployed: "ارسال‌شده · غیرفعال",
  activating: "در حال فعال‌سازی",
  active: "فعال",
  deactivating: "در حال غیرفعال‌سازی",
  deployment_unknown: "نیاز به تطبیق وضعیت",
  activation_unknown: "نیاز به تطبیق وضعیت",
};
export const nodeKinds = {
  manual: ["شروع دستی", "play"],
  webhook: ["دریافت پیام", "webhook"],
  schedule: ["زمان‌بندی", "clock"],
  set: ["تنظیم داده", "sliders"],
  http_request: ["درخواست HTTP", "globe"],
  ollama_chat: ["هوش مصنوعی محلی", "spark"],
  if_equals: ["بررسی شرط", "branch"],
  respond: ["برگرداندن پاسخ", "check"],
};
export const stageLabels = {
  thinking: "دارم به درخواستت فکر می‌کنم…",
  planning: "در حال طراحی Workflow با مدل محلی…",
  validating: "دارم گره‌ها و اتصال‌ها را بررسی می‌کنم…",
  compiling: "در حال آماده‌کردن Workflow برای n8n…",
};
export const warningLabels = {
  webhook_public:
    "این Webhook احراز هویت ندارد؛ پیکربندی فعلی برای استفادهٔ محلی است.",
  ollama_result: "متن خروجی مدل در message.content قرار می‌گیرد.",
  external_http:
    "این Workflow می‌تواند اطلاعات را به مقصدهای HTTP نمایش‌داده‌شده بفرستد.",
};
export function errorMessage(error) {
  if (error.status === 401)
    return "کلید فضای کاری درست نیست یا اتصال منقضی شده است.";
  if (error.status === 429)
    return "مدل مشغول است. بعد از تمام‌شدن درخواست فعلی دوباره بفرست.";
  if (error.status === 409)
    return "وضعیت یا نسخه عوض شده است. اطلاعات را تازه کن و دوباره بررسی کن.";
  if (error.status === 422)
    return "درخواست معتبر نیست. ورودی، کار اصلی و خروجی را دقیق‌تر مشخص کن.";
  if (error.status === 413)
    return "پیام بیش از اندازه طولانی است. لطفاً کوتاه‌ترش کن.";
  if (error.detail?.code === "not_configured")
    return "کلید API اتصال n8n هنوز در تنظیمات پروژه وارد نشده است.";
  if (error.status === 503)
    return "سرویس در دسترس نیست. بخش اتصال‌ها را بررسی کن؛ ارسال دوباره خودکار انجام نمی‌شود.";
  return "ارتباط کامل نشد. وضعیت گفتگو و اتصال به برنامه را بررسی کن.";
}
export class EventParser {
  constructor(onEvent) {
    this.buffer = "";
    this.onEvent = onEvent;
  }
  push(chunk) {
    this.buffer += chunk;
    // The server emits LF; also accept CRLF, even when split across reads.
    let match;
    while ((match = /\r?\n\r?\n/.exec(this.buffer))) {
      const block = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) this.onEvent(JSON.parse(data));
    }
    if (this.buffer.length > 600000) throw new Error("Oversized event");
  }
}
export function nodeDetail(node) {
  if (node.kind === "webhook") return `${node.method} / ${node.path}`;
  if (node.kind === "schedule") return `فاصلهٔ دقیقه‌ای: ${node.every_minutes}`;
  if (node.kind === "ollama_chat") return `ورودی: ${node.input_path}`;
  if (node.kind === "if_equals") return `${node.input_path} = ${node.equals}`;
  if (node.kind === "http_request") return `${node.method} ${node.url}`;
  if (node.kind === "set")
    return node.assignments.map((item) => item.name).join("، ");
  if (node.kind === "respond") return `JSON · ${node.status_code}`;
  return "اجرا از ویرایشگر n8n";
}
