# Master AI Agent Builder — MVP

ساخت Workflowهای کوچک n8n با Python، FastAPI، LangGraph و مدل محلی
`qwen3.5:9b` در Ollama؛ بدون نیاز به API پولی GPT یا Claude.

مدل یک **IR محدود و ساختاریافته** می‌سازد. اعتبارسنج پایتون آن را بررسی می‌کند و
compiler قطعی، JSON مربوط به n8n را تولید می‌کند. ساخت، ارسال و فعال‌سازی سه عملیات
جدا هستند. **هیچ Workflow با درخواست ساخت یا ارسال، فعال نمی‌شود.**

## وضعیت بررسی‌شده روی این سیستم

در ۱۹ نوامبر ۲۰۲۶:

- محیط مجازی پروژه با Python 3.13.17 و وابستگی‌های قفل‌شده ساخته شد.
- Ollama پاسخ داد و مدل دقیق `qwen3.5:9b` با اندازهٔ 9B و کوانتایز Q4_K_M موجود بود.
- تست واقعی مدل → IR معتبر → JSON غیرفعال n8n در اولین تلاش موفق شد.
- Docker در سیستم پیدا نشد و n8n روی پورت 5678 اجرا نبود.
- برای اتصال واقعی، نصب Docker و ساخت کلید API در n8n هنوز لازم است.
- هیچ Workflow واقعی ارسال یا فعال نشده است.

نتیجهٔ آزمایش واقعی در `outputs/live-ollama-smoke.json` ذخیره می‌شود.

## ۱. نصب و تنظیم محلی

از پوشهٔ پروژه اجرا کنید:

```bash
uv sync --frozen --group dev
uv run python scripts/bootstrap.py
uv run python scripts/doctor.py
```

اسکریپت bootstrap فایل خصوصی `.env` و یک `MASTER_API_KEY` تصادفی می‌سازد؛
فایل موجود را بازنویسی نمی‌کند و کلید را در خروجی چاپ نمی‌کند. این مرحله در محیط
فعلی انجام شده است. خروجی ناموفق doctor تا آماده‌شدن Docker و n8n طبیعی است.

تنظیمات مهم در `.env`:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:9b
N8N_BASE_URL=http://127.0.0.1:5678
N8N_OLLAMA_BASE_URL=http://127.0.0.1:11434
N8N_API_KEY=
HTTP_ALLOWED_HOSTS=[]
```

دو آدرس Ollama جدا هستند: اولی از دید برنامهٔ پایتون، دومی از دید Workflow
داخل n8n. تنظیمات پیش‌فرض برای همین سیستم Linux و شبکهٔ host در Docker هستند.
مدل موجود است؛ نیازی به دانلود دوبارهٔ آن نیست. برای بررسی دستی:

```bash
ollama list
```

## ۲. اجرای API

```bash
uv run uvicorn master_agent.api:app --host 127.0.0.1 --port 8000 --workers 1
```

راهنمای تعاملی API در [Swagger](http://127.0.0.1:8000/docs) است.
روی Authorize بزنید و مقدار `MASTER_API_KEY` از `.env` را وارد کنید.
مسیرهای `/api/v1/*` به این کلید نیاز دارند. `/healthz` فقط زنده‌بودن برنامه را نشان می‌دهد؛
`/api/v1/system` اتصال به Ollama و n8n را جدا بررسی می‌کند.

**یک process و یک worker برای هر دیتابیس اجرا کنید.** از `--reload` هنگام ارسال یا
فعال‌سازی استفاده نکنید. برنامهٔ محلی با `.env` فعلی آمادهٔ اجراست.

## ۳. اولین ساخت، حتی بدون n8n

در ترمینال دوم و با API روشن:

```bash
uv run python examples/build_client.py --prompt 'یک دستیار فارسی بساز که پیام را با Webhook از body.message بگیرد، با Ollama جواب بدهد و پاسخ JSON برگرداند.'
```

یا بدون استفاده از مدل، یک IR آماده را کامپایل کنید:

```bash
uv run python examples/build_client.py --ir examples/assistant.ir.json
```

شناسهٔ ساخت و دو فایل در `outputs/` برگردانده می‌شود: گزارش ساخت و JSON مناسب
Import در n8n. وضعیت ساخت `draft` است و هیچ درخواست نوشتنی به n8n ارسال نشده است.
نمونهٔ `examples/manual.ir.json` برای اجرای دستی در ویرایشگر n8n نیز آماده است.

## ۴. راه‌اندازی n8n با Docker

این Compose برای **Linux محلی** طراحی شده است. `network_mode: host` به n8n اجازه
می‌دهد به Ollama فعلی روی `127.0.0.1:11434` برسد، بدون تغییر تنظیمات سرویس Ollama.
n8n فقط روی `127.0.0.1:5678` گوش می‌دهد. برای Windows/macOS باید شبکه و آدرس
Ollama را متناسب با Docker Desktop تنظیم کنید؛ این پیکربندی برای آن‌ها آزمایش نشده است.

اگر Docker نصب نیست، ابتدا مطابق [راهنمای رسمی Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
Docker Engine و Compose را نصب کنید. نصب سراسری یا تغییر سرویس سیستم توسط پروژه انجام نمی‌شود.
سپس:

```bash
docker compose config --quiet
docker compose up -d n8n
docker compose ps
```

نسخهٔ هدف `2.48.5` است و در `.env` قابل تغییر است. داده‌ها در volume به نام
`n8n_data` می‌مانند؛ کلید رمزگذاری n8n نیز در همین volume حفظ می‌شود.

1. [n8n محلی](http://localhost:5678) را باز کنید و حساب مالک بسازید.
2. در Settings → n8n API یک API key بسازید.
3. مقدار آن را در `N8N_API_KEY` داخل `.env` بگذارید.
4. API پایتون را بعد از تغییر `.env` دوباره اجرا کنید.
5. `uv run python scripts/doctor.py` را اجرا کنید.

دسترسی‌های لازم: ساخت، خواندن و فهرست Workflow؛ برای فعال/غیرفعال‌سازی و خواندن
اجرای‌ها، scope مربوط به همان عملیات را هم بدهید. نام دقیق scope را در نسخهٔ
n8n نصب‌شده بررسی کنید. برای مرحلهٔ ساخت و ارسال نیازی به scope فعال‌سازی نیست.
[مستند احراز هویت n8n](https://docs.n8n.io/connect/n8n-api/authentication.md)

برای اجرای خود builder در Docker نیز Dockerfile و پروفایل اختیاری موجود است:

```bash
docker compose --profile app up -d --build
```

هم‌زمان نسخهٔ پایتون محلی را روی پورت 8000 اجرا نکنید. دیتابیس نسخهٔ کانتینری
در volume جدا به نام `builder_data` است.

## ۵. ارسال غیرفعال و بازبینی

```bash
uv run python examples/build_client.py --ir examples/assistant.ir.json --deploy
```

این دستور Workflow را در n8n ایجاد می‌کند و شناسهٔ ساخت را چاپ می‌کند؛
وضعیت محلی `deployed` و وضعیت n8n غیرفعال است. JSON خام ارسالی به API شامل
فیلدهای فقط‌خواندنی مثل `active` نیست؛ فایل Import شامل `active:false` است.

طرح، مسیرها، prompt، مقصد درخواست‌ها و اتصال گره‌ها را در n8n بررسی کنید.
اگر Workflow را در n8n ویرایش کنید، فعال‌سازی از این برنامه به‌علت اختلاف محتوا
متوقف می‌شود؛ تغییر را در IR ثبت کنید و ساخت جدید بسازید.

## ۶. فعال‌سازی فقط با تأیید مالک

این مرحله **اختیاری و جدا** است؛ فقط پس از بازبینی و تصمیم خودتان اجرا کنید:

```bash
uv run python scripts/review_activation.py BUILD_ID
```

اسکریپت نسخهٔ n8n را می‌خواند، آن را با خروجی compiler مقایسه می‌کند، فایل بازبینی
را ذخیره می‌کند و از شما می‌خواهد عبارت دقیق `ACTIVATE <workflow-id>` را تایپ کنید.
ورودی خالی، قطع ورودی یا عبارت دیگر، هیچ درخواستی برای فعال‌سازی ارسال نمی‌کند.
برنامه پیش از درخواست نهایی، اثرانگشت و `versionId` را دوباره بررسی می‌کند.

مسیر HTTP فعال‌سازی نیز فقط با کلید مالک، `confirm: true` از نوع boolean و هر دو
اثر انگشت معتبر کار می‌کند. کلید مالک و این اسکریپت در اختیار ابزارهای مدل نیستند.
این کنترل، تأیید caller احرازشده را ثبت می‌کند؛ API امکان تشخیص حضور فیزیکی انسان ندارد.

پس از تأیید و فعال‌سازی دستیار نمونه، می‌توان آن را فراخوانی کرد:

```bash
curl --fail-with-body http://localhost:5678/webhook/persian-assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"سلام؛ در یک جمله خودت را معرفی کن."}'
```

متن پاسخ در `message.content` از خروجی Ollama قرار دارد. Webhook این MVP احراز هویت
ندارد و Compose ارائه‌شده برای دسترسی محلی است. تا قبل از تأیید فعال‌سازی،
این مسیر production آماده نیست؛ برای تست دستی می‌توانید از حالت Test در ویرایشگر n8n استفاده کنید.

## تست‌ها و عیب‌یابی

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python scripts/smoke_ollama.py
```

تست‌های عادی آفلاین‌اند و رفتار n8n را شبیه‌سازی می‌کنند، از جمله تأیید ناقص،
تغییر نسخه بعد از بازبینی، قطع ارتباط، هم‌زمانی و جلوگیری از ارسال تکراری.
اسکریپت smoke واقعاً Ollama را فراخوانی می‌کند و با n8n ارتباط نمی‌گیرد.
به‌دلیل نبود Docker، Import و اجرای واقعی داخل n8n در این سیستم هنوز بررسی نشده است.

- `503 / not_configured`: مقدار `N8N_API_KEY` را تنظیم و API را دوباره اجرا کنید.
- `model_missing`: نام دقیق مدل در `.env` باید با `ollama list` یکسان باشد.
- کندی مدل: درخواست ساخت ممکن است چند دقیقه طول بکشد؛ بودجهٔ پیش‌فرض سه تلاش است.
  خروجی ساخت را بازبینی کنید؛ اعتبار ساختاری، درستی معنایی خواسته را تضمین نمی‌کند.
- `409` هنگام فعال‌سازی: محتوا، نسخه یا وضعیت عوض شده است؛ بازبینی تازه بگیرید.
- `deployment_unknown`: پاسخ ارسال نامشخص است؛ دوباره POST نزنید. شناسهٔ Workflow
  را در n8n پیدا کنید و `POST /api/v1/builds/BUILD_ID/reconcile` با
  `{"workflow_id":"N8N_WORKFLOW_ID"}` بفرستید.
- `activation_unknown`: با همان مسیر و بدنهٔ `{}` وضعیت n8n فقط خوانده و تطبیق داده می‌شود.

## محدودهٔ MVP و فایل‌ها

این نسخه یک دستیار **بدون حافظه و ابزار** می‌سازد. Telegram، ایمیل، Credential، RAG،
کد دلخواه، loop، چند trigger و اتصال چند ورودی فعلاً پشتیبانی نمی‌شوند.
HTTP خارجی به‌صورت پیش‌فرض بسته است. برای یک میزبان مورداعتماد، مثلاً
`HTTP_ALLOWED_HOSTS=["httpbin.org"]` را تنظیم کنید؛ فقط HTTPS روی 443 و بدون redirect مجاز است.

- `src/master_agent/ir.py`: schema نسخه‌دار IR و گره‌های مجاز.
- `validator.py` و `compiler.py`: اعتبارسنجی و تبدیل قطعی.
- `planner.py`: گراف تولید، اعتبارسنجی و اصلاح محدود با LangGraph.
- `clients.py`: کلاینت‌های Ollama و n8n REST API.
- `repository.py` و `service.py`: SQLite، وضعیت‌ها، تطبیق و تأیید.
- `api.py`: مسیرهای FastAPI و احراز هویت.
- `tests/`: تست‌های خودکار؛ `examples/`: نمونه‌های قابل استفاده.
- [معماری، قرارداد API و منابع رسمی](docs/architecture.md).

فایل `.env`، دیتابیس، خروجی‌ها و پوشهٔ `work/` وارد git نمی‌شوند. داده‌های n8n
در volume و داده‌های builder در SQLite ذخیره می‌شوند؛ پیش از تغییر نسخه از آن‌ها نسخهٔ پشتیبان بگیرید.
