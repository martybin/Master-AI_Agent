# Master Local Agent

Version 0.3 adds a native host runtime, a bounded tool loop, encrypted integration
connections and an extensible n8n compiler. The UI and API run in one Python
process. Ollama remains the only model provider. Run one worker/process per DB.

## Start on the host

```bash
uv sync --frozen --group dev
uv run python scripts/bootstrap.py
uv run master-local doctor
uv run master-local serve --workspace /absolute/path/to/automation-workspace
```

Open `http://127.0.0.1:8000`, enter the owner key from `.env`, create a chat and
open **Local Agent**. Nothing is uploaded to a cloud model. Calls to
configured integrations still transmit the data needed by those integrations.
The supplied n8n Docker service is independent; host tools need the Python
runtime on the host. Running Master inside Docker gives container access only.
The project does not mount the Docker socket, elevate privileges or install
system services automatically.

Configuration additions:

```dotenv
LOCAL_WORKSPACE=/absolute/path/to/automation-workspace
LOCAL_MAX_STEPS=10
LOCAL_PLUGIN_MODULES=[]
# Optional: default is vault.key beside DATABASE_PATH
# LOCAL_VAULT_KEY=data/vault.key
```

`master-local doctor` checks the exact installed model and authenticated n8n
read access. It reports Docker availability without printing keys. A successful
model check does not mean n8n or Telegram is configured.

## Access and cancellation

Access is per chat, initially **off**. Owner-only controls grant `workspace` or
`system`, normally for four hours (API supports 1–24 hours). `system` requires
the actual boolean `confirm:true`. Models cannot grant themselves permission.

- `workspace`: list/read/write files within the resolved workspace and call
  owner-configured HTTP connections. Symlinks outside the root are rejected.
- `system`: additionally run commands and address files outside the root, as
  the current OS user. No automatic `sudo` or privilege elevation is added.
- Inspection, connection aliases, node discovery, draft creation and n8n read
  tools do not need filesystem permission. Explicit inactive deployment uses
  the existing guarded build service.

**System mode is full OS-user execution, not a security sandbox.** Protecting
`.env`, the database, vault and `.git` in file tools, filtering command environment
keys and redacting known secrets reduce accidental exposure. They cannot stop
arbitrary authorized code from reading those files, encoding their contents or
calling an API itself. Owner API approval protects the normal workflow path;
it does not isolate an unrestricted local process. For untrusted workloads use
a separate OS account/container with limited mounts and network access.

Commands use argument arrays, bounded output, a 1–120 second deadline and a new
process group on POSIX. **Stop** cancels the chat task and kills its current
command group. Browser disconnect alone does not cancel it. Revoking a grant
blocks subsequent tools; use cancellation to stop a tool already running.
Cancellation cannot undo side effects already completed by external programs.

Every call records tool name, argument field names, status and timing; audit
does not store command text or secret values. Conversation text and generated
workflows are ordinary SQLite records, not encrypted private document storage.

Before accepting a final reply after tool use, a separate structured model call
compares the owner's request with actual redacted arguments/results, without the
assistant's success narrative. An incomplete review returns to the tool loop;
a subsequent decision is constrained to a tool action to finish the missing work;
a blocker is reported as unfinished work. This is an additional model check,
not a proof of semantic correctness. The live smoke checks real file contents
and observed tool calls independently of the model's text.

## Telegram: bot or personal account

Connections are created in the owner panel, never by sending secrets in chat.
Aliases such as `support_bot` are what the model sees. Credentials and personal
StringSessions are Fernet-encrypted in SQLite; `vault.key` is created with mode
0600. Back up **both** the database and vault key privately. Losing the key
prevents decrypting connections/inbox; startup refuses to silently replace a
missing key when encrypted records exist. This is local encrypted storage, not
an OS keychain or hardware security module.

For a **bot**, create a BotFather token, choose **Telegram bot**, register it, then
verify. For a **personal account**, obtain your own Telegram `api_id`/`api_hash`,
choose **Personal account**, register it, then select **Sign in**. Enter the phone,
Telegram login code and optional 2FA password in the panel. The login flow
supports the code/password path; Telegram may refuse delivery or require a
different sign-in method. Pending sign-ins expire after ten minutes. Codes,
phone hashes and passwords are not persisted. The authorized session is kept
encrypted. Never put these credentials in a prompt, git or workflow JSON.

Ask in chat, for example:

> برای اتصال support_bot یک دستیار فارسی بساز که به پیام‌ها کوتاه پاسخ بدهد.

`connections.list` discovers the alias; `workflow.telegram_assistant` creates
an inactive webhook → Ollama → response draft bound to the real connection.
This is not a claim that Telegram itself has no connector. Telegram polling is
handled locally, while n8n executes the automation.

1. Review the draft and deploy it inactive using the existing UI.
2. Review and explicitly activate the exact n8n version in the owner dialog.
3. In Local Agent → Automation, select connection and build, then save binding.
4. For personal accounts, load dialogs and select chat IDs. A nonempty explicit
   allowlist is required. For bots, an empty list allows all received chats.
5. Default reply path is `message.content`, the Ollama JSON output. Empty means
   process the workflow without sending a Telegram response.
6. Check automatic-processing confirmation and start receiving messages.

Startup checks the remote workflow's content, active flag and published version.
A bridge never activates a workflow. A bot with an existing webhook is refused;
Master never deletes it automatically. Bot polling and webhook delivery cannot
be used simultaneously for the same bot. Do not run another poller for that bot.

No public inbound URL is needed: Master long-polls the Bot API, or polls selected
personal chats, then calls the local n8n webhook. Both Master and n8n must stay
running. Authorized enabled bindings resume after restart; this continues the
owner's existing permission, it does not grant a new activation.

The ready-made bridge handles **new incoming text messages** and replies to the
captured originating chat. Personal chats establish a latest-message baseline on
first start; historical messages are not backfilled. Stored cursors support
later restarts. Bot processing may include Telegram's queued pending updates.
Nontext media is skipped. Telegram API availability, account restrictions,
flood limits, dialog access and bot privacy settings still apply.

## Delivery and recovery

### Planning birthdays for personal contacts

A request to congratulate personal Telegram contacts on their birthdays first
checks real connection prerequisites. With no personal account, an unfinished
login, or no birthday source, the chat returns an initial design and specific
setup/data questions. It does not keep listing the same empty connections.
Passwords and codes still belong in the Connections panel.

The suggested design is a daily date check, selection of today's recipients,
per-contact/year deduplication, then sending and recording the outcome. This
planning check does **not** create or enable a scheduled birthday sender.
Dates, calendar/timezone, send time and an actual outbound integration must be
specified and implemented/reviewed before activation. The existing incoming
text-reply template is excluded from these requests; it would not implement
the requested job.

Bot credentials do not grant personal-account contact access. Telegram birthday
visibility depends on the contact's privacy settings; missing dates cannot be
inferred. The official [profile API](https://core.telegram.org/api/profile)
describes available birthday information and a nearby-birthday query, not a
complete database of every contact's birthday. An owner-supplied CSV/JSON can
provide dates that the owner already has; confirm its calendar before use.

### Repeated reads

Within a chat turn, identical successful read-only tool calls are remembered.
A duplicate reuses the previous evidence without a second external read, and
the model gets one correction opportunity. Repeating the same read again ends
with a specific loop diagnostic instead of exhausting the entire step budget.
Successful writes/commands invalidate the read history, allowing a fresh read
after a real change. The overall step budget still applies to distinct calls.

### Message delivery

The inbox encrypts payload/reply, deduplicates event IDs and durably captures
before advancing polling offsets. Only one delivery claim can run at a time.
Known pending deliveries resume automatically. Unknown outcomes do not.

If a webhook POST or Telegram send may have completed before the response was
lost, delivery is marked `uncertain`. The activity panel offers an explicitly
confirmed retry and explains possible duplicate side effects. A cached reply
is checkpointed before sending; retries after a send failure reuse it instead
of rerunning the workflow. A process interrupted during delivery also becomes
uncertain on restart. Exactly-once effects across SQLite, n8n and Telegram are
not promised. Changing a binding does not reroute an older delivery to a new
workflow. Inspect and resolve uncertain results before retrying.

## Advanced workflows and installed nodes

Small IR 1.0 keeps its earlier strict tree constraints and HTTPS allowlist.
`NativeSpec` is a separate extensible path: registered types/versions, node
parameters, native credential IDs and n8n connections including branches, joins,
multiple triggers and cycles. It compiles deterministic node IDs and JSON into
the same local build repository and the same inactive deployment/review path.
The workflow has an execution timeout of 1200 seconds. The model has no
activation tool, credential creation tool or raw token field.

Auxiliary `ai_languageModel`, `ai_memory` and `ai_tool` connections are supported
in their native direction into the consuming agent. They count as attached
capabilities when validating reachability, while disconnected nodes are rejected.

The initial catalog contains twelve common node types. Their seed schemas are
intentionally partial. Discover the installed n8n node metadata rather than
assuming every integration/version exists. On the supplied Docker setup:

```bash
mkdir -p work
docker compose exec -T n8n n8n export:nodes --output=/tmp/master-nodes.json
docker compose cp n8n:/tmp/master-nodes.json work/n8n-nodes.json
```

In Local Agent → Access, grant workspace access and import
`work/n8n-nodes.json` (path relative to the configured workspace; use an absolute
path within it if needed). The importer converts metadata into parameter
schemas and retains original property descriptions. `catalog.search` and
`catalog.describe` expose registered nodes to the model. `/types/nodes.json`
is an internal session-authenticated n8n route, not an assumed public API endpoint.
Catalog import does not install missing n8n packages.

`POST /api/v1/local/catalog` can register a custom node descriptor with a JSON
Schema. External schema references are rejected. `NativeSpec` credentials map
credential type to **an existing n8n credential ID**. Personal Telegram sessions
belong to Master, not n8n. Native bot nodes instead use n8n's own Telegram
credential and may need public HTTPS webhooks. Do not enable both native bot
triggers and the local polling bridge for the same bot.

Credential display names added by n8n are accepted as metadata; ID/type changes
still block approval. ID-based lookup follows the
[official credential helper](https://github.com/n8n-io/n8n/blob/master/packages/cli/src/credentials-helper.ts).

Schemas validate structure, registered versions, links and reachability. They
do not prove node semantics, expression correctness, credential access or live
runtime compatibility. Imported conditional/dynamic node properties can need
manual correction. Review, manually test in n8n and inspect execution results.
Qwen 9B is usable locally; it is not a guarantee of flawless complex planning.

## HTTP and Python extensions

An HTTP connection has an owner-configured base URL and optional secret header.
The model uses only its alias. Credential injection happens at runtime; URL joins
must retain the exact scheme/host/port and redirects are not followed. Configure
plain HTTP only for trusted local services. `http.request` supports authenticated
GET/POST/PUT/PATCH/DELETE, so it can perform real mutations when the owner grants
access and requests them. Failed mutations may have unknown external outcomes.
Timeouts/network failures and 5xx responses on HTTP mutations stop the chat turn
with an uncertain-outcome message; the model is not allowed to retry that
mutation automatically in the same turn. Inspect the remote state before
requesting a new attempt.

For integrations beyond registered nodes/APIs, use a trusted Python module:

```python
from pydantic import BaseModel


class Args(BaseModel):
    text: str


async def summarize(args):
    return {"length": len(args.text)}


def register(registry):
    registry.register("my.text_length", "Measure text", Args, "workspace", summarize)
```

Install the module in the runtime and set `LOCAL_PLUGIN_MODULES=["my_plugin"]`.
The registry enforces its declared scope and schema, redacts results and records
audit events. Modules run as trusted Python code at startup, not in isolation;
do not install arbitrary modules based on model output. Async handlers run in
the event loop; sync handlers use a thread. Only core terminal tools support
process-group cancellation; plugin authors must implement their own cleanup.

## Owner API

All paths below are relative to `/api/v1/local` and require the owner Bearer key:

- `GET /status?chat_id=UUID`, `GET /tools`: control state and published schemas.
- `PUT /grants/{chat_id}`: `{mode, confirm, hours}`; chat must exist.
- `POST /tools/execute`: `{chat_id, name, arguments}`; applies the same grant.
- `GET/POST /connections`, `POST /connections/{alias}/verify`.
- `POST /connections/{alias}/login/start`: `{phone}`.
- `POST /connections/{alias}/login/finish`: `{code, password}`; supports 2FA stage.
- `POST /connections/{alias}/login/cancel`, `GET /connections/{alias}/dialogs`.
- `PUT /bindings/{alias}`: `{build_id, allowed_chat_ids, reply_path}`.
- `POST /bindings/{alias}/start`: `{confirm:true}`; `POST .../stop`.
- `GET /catalog?query=...`, `GET /catalog/describe?node_type=...`, `POST /catalog`.
- `POST /native-builds`: NativeSpec object; always a local draft.
- `POST /inbox/{id}/retry`: `{confirm:true}` for pending/uncertain delivery only.

This remains a local single-owner application, not a hosted multi-tenant system.
Connection editing/removal, key rotation, media bridges, per-tool approval UI
and a durable distributed job queue are not implemented in this version.

## Validation and primary references

```bash
make test
make test-ui
make check
uv build --out-dir work/dist
```

`make smoke-local` additionally calls the real installed Ollama model in a
disposable workspace/database. It verifies actual file inspection/write/read
and connection-specific bot/personal drafts using explicitly fake credentials.
It does not log in, send Telegram messages or deploy/activate n8n workflows.
Failures are nonzero, including a model claiming success without creating the
expected file. Temporary smoke data is removed when the script exits normally.

Automated tests cover real local subprocesses/path grants/cancellation, mocked
Bot API and personal 2FA/session handling, exact activation guards, catalog
extensions, native compilation and uncertain delivery recovery. Mocked Telegram
and n8n tests do not establish a real login or live n8n execution.

- [Telethon signing in](https://docs.telethon.dev/en/stable/basic/signing-in.html)
- [Telethon client API](https://docs.telethon.dev/en/stable/modules/client.html)
- [Telegram Bot API polling](https://core.telegram.org/bots/api#getupdates)
- [n8n Telegram credentials](https://docs.n8n.io/integrations/builtin/credentials/telegram/)
- [n8n CLI commands](https://docs.n8n.io/hosting/cli-commands/)
