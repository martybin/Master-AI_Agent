# Architecture and API contract

BisCode 0.4 adds owner-approved local coding and Python agent projects alongside
this n8n pipeline. See [coding, permissions and continuation](coding.md). The
existing compiler, inactive deployment and explicit activation contract remain.

## Scope

Master Local Agent turns natural language into reviewed n8n workflows and can
use owner-granted host tools. Python/FastAPI serves a local UI; the small-IR
planner uses a compiled LangGraph `StateGraph`. A bounded chat loop discovers
tools, observes their results and continues with the local Ollama model.
There is no cloud provider dependency. Generated small-IR agents remain
stateless; advanced native workflows can use installed integration/memory nodes.
See [the local runtime contract](local-agent.md) for tools, encrypted connections,
Telegram delivery, native schemas, access boundaries and extension points.

The local web UI is served by the same process at `/`, using bundled HTML, CSS,
JavaScript and a local OFL-licensed Vazirmatn font. It adds durable conversations
for the builder; it does not add runtime memory to generated agents. See
[the chat UI guide](chat-ui.md) for the endpoint and browser session contracts.

`ChatService` first requests a small `ChatDecision` structured response from
Ollama. A conversational reply is saved directly; a build decision calls the
same validated planner with recent messages and the previous IR. A tool decision
uses the published registry and owner grant. An explicit deploy request can
create an inactive workflow; activation is never a chat tool. Revisions create
new draft builds, preserving prior artifacts.
Browser input and model output are rendered as text or a small safe Markdown
subset; arbitrary HTML is not inserted. Owner keys are never embedded in assets.

Chat uses authenticated fetch and SSE stage events, including heartbeats. The
worker is tracked independently of the streaming response, so disconnecting a
browser does not cancel a build. Process shutdown cancels active tasks and
records an interrupted turn. The chat and direct-build paths share one inference
lock. An idempotent message request ID prevents duplicate model calls after replay.

## Build pipeline

1. `OllamaClient.models()` checks that the exact configured model is installed.
2. LangGraph runs `generate -> validate -> compile`. Validation failure routes
   back to generation with structured feedback, at most `PLANNER_MAX_ATTEMPTS`.
3. Ollama `/api/chat` receives the IR JSON Schema in `format`, temperature zero,
   a bounded context and output budget, and `think=false`. It receives no tools,
   API keys, filesystem access or n8n client. This describes the small-IR planner;
   the outer chat router separately receives tool schemas and redacted results.
4. Pydantic rejects unknown properties and node types. Semantic validation
   rejects invalid topology, expression injection and unauthorized destinations.
5. The pure compiler validates again and produces a deterministic n8n artifact.
6. SQLite stores the IR, compiled artifact, SHA-256, warnings and lifecycle state.

The model call is not guaranteed deterministic. Compilation is deterministic for
the same validated IR and compiler settings. JSON is canonicalized for hashes;
node UUIDs use UUIDv5. Node/edge ordering does not affect the emitted workflow.
The compiler does not resolve DNS or contact external services.

## Supported IR 1.0

- `manual`: Manual Trigger v1.
- `webhook`: Webhook v2, GET/POST, explicit response node.
- `schedule`: Schedule Trigger v1.2, n8n minute intervals 1–59.
- `set`: Edit Fields v3.4, scalar literals and explicit input references.
- `http_request`: HTTP Request v4.2, fixed HTTPS URL, GET or JSON POST.
- `ollama_chat`: HTTP Request v4.2 calling the configured native Ollama `/api/chat`.
- `if_equals`: IF v2.2, string comparison, true output 0 and false output 1.
- `respond`: Respond to Webhook v1.4, returns the current JSON object.

Graphs must be trees with one trigger and up to 20 nodes. There are no cycles,
joins, multiple edges per port or implicit branches. Every webhook leaf must be
a response. References read the current item (`body.message`, `message.content`),
not arbitrary JavaScript or data from arbitrary previous nodes. HTTP and Ollama
replace the input item with their response. Set can preserve input fields.
The schema is available from `GET /api/v1/ir/schema` and contains all parameters.

## Lifecycle and approval

`draft -> deploying -> deployed -> activating -> active` is the normal path.
Build and compile endpoints cannot deploy. Deploy calls only POST `/workflows`
and sends `name`, `nodes`, `connections`, `settings`. The import artifact includes
`active:false`; the API payload omits this read-only property.

Activation is a separate owner action:

1. `/activation-review` fetches the deployed workflow and compares executable
   content against the compiled draft. Known server-derived binary-mode and v1
   engine metadata are accepted and included in the review hash. Unknown executable node options,
   credentials, changed parameters, settings or connections fail closed.
2. The response contains the remote snapshot, local workflow SHA-256 and a
   review SHA-256 including n8n's version. The review hash is persisted.
3. `/activate` requires the owner's bearer key, a strict JSON boolean `confirm:true`
   and both fingerprints. Strings such as `"true"` and numbers are rejected.
4. SQLite atomically claims the transition and rejects concurrent activation.
5. The service fetches n8n again and checks the review hash. It sends the exact
   reviewed `versionId` to `/activate`, preventing a later edited version from
   being silently published. n8n's authorization and webhook conflict checks
   remain authoritative.

`confirm:true` is an assertion by the authenticated owner. The HTTP API cannot
prove a human was present. Activation is not a model tool. The interactive
approval script requires a workflow-specific phrase. A system grant permits
arbitrary OS-user commands: code with those privileges can read owner files and
make HTTP requests itself. The normal approval API is not an isolation boundary
against an unrestricted local process. See the local runtime access contract.

## Ambiguous mutation results

There are no automatic POST retries. A timeout may mean that n8n completed the
operation but its response was lost. The record becomes `deployment_unknown` or
`activation_unknown`. Subsequent mutation attempts return 409. Reconciliation
performs GET only, verifies content, and records the observed activation state.
For an uncertain create, the owner supplies the workflow ID found in n8n. If the
owner verifies that no workflow was created, create a new build; the old record
remains an audit record rather than guessing a safe retry.

Interrupted `deploying`, `activating` and `deactivating` states become unknown
on process restart. SQLite `BEGIN IMMEDIATE` serializes state claims. Run exactly
one worker and one builder process per database. This runtime does not support
horizontal scaling or shared network filesystems. Local-runtime tables are
created additively, preserving existing chat/build records.

## REST routes

All `/api/v1/*` routes use `Authorization: Bearer <MASTER_API_KEY>`.
`/healthz` is a public liveness endpoint, not an integration readiness check.

- `GET /system`: Ollama/model and authenticated n8n read checks.
- `GET /ir/schema`: complete Pydantic JSON Schema.
- `POST /ir/validate`: IR object; semantic report with `valid`, `errors`, `warnings`.
- `POST /builds`: `{ "prompt": "..." }`, generates/compiles a draft, returns 201.
- `POST /builds/from-ir`: IR object; offline compilation and persistence, 201.
- `GET /builds/{id}`: full local record.
- `GET /builds/{id}/workflow?format=import|api`: inactive artifact or create payload.
- `GET /builds/{id}/events`: local lifecycle audit trail.
- `POST /builds/{id}/deploy`: creates one inactive workflow; repeat success is local/idempotent.
- `POST /builds/{id}/activation-review`: read-only n8n review and persisted review hash.
- `POST /builds/{id}/activate`: `{ "confirm": true, "workflow_sha256": "...", "review_sha256": "..." }`.
- `POST /builds/{id}/deactivate`: separate deactivation operation.
- `POST /builds/{id}/reconcile`: `{ "workflow_id": "..." }`, or `{}` when ID is known.
- `GET /builds/{id}/executions`: last 20 execution summaries, without execution data.

Route paths above are relative to `/api/v1`. Errors: 401 auth, 404 missing build,
409 state/review conflicts, 413 oversized body, 422 schema/IR/planning failure,
429 model busy, 503 integration unavailable/uncertain. Planning is synchronous
from the HTTP caller's perspective; the request timeout must cover its retry
budget. Only one inference request runs at once. There is no background job queue.

## Small-IR boundaries

HTTP destination allowlists match exact configured hostnames. HTTPS/443 is
required; redirects, private literal IPs, local hostnames and userinfo are
rejected. Trusted allowed hosts can still resolve to private IPs or change DNS;
this is not a network sandbox. Use DNS and egress controls before expanding to
untrusted users. The Ollama destination is a separate trusted operator setting.

There are no credentials, arbitrary headers, Code/Execute Command/community
nodes or raw expressions in IR. The compiler owns every generated expression
and JSON-escapes literals. All paths use a restricted property grammar and
reject prototype traversal. Small-IR webhooks have no authentication; the
provided local-only n8n listener is part of the deployment scope.

Local configuration and SQLite may contain sensitive prompts and outputs.
`.env`, SQLite, scratch and generated outputs are ignored by git. API and model
error responses omit upstream bodies; secrets are not sent to the planner.
There is no CORS enablement or cloud inference fallback.

## Verification and sources

Unit and integration-style tests use `httpx.MockTransport` to verify activation
guards, mutation paths, deterministic compilation, repair budgets, persistence,
concurrency, ambiguous outcomes and input rejection. They do not prove runtime
compatibility with an installed n8n instance. `scripts/smoke_ollama.py` explicitly
tests the actual local model and never contacts n8n.

Target image: n8n **2.48.5**. Node parameters and public API payloads were checked
against the official source. n8n still supports `/activate` and `/deactivate` in
this target; newer documentation also uses publish/unpublish terminology. Future
upgrades require contract and real import/execution verification.

- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [n8n API authentication](https://docs.n8n.io/connect/n8n-api/authentication.md)
- [n8n 2.48.5 create contract](https://github.com/n8n-io/n8n/blob/n8n%402.48.5/packages/cli/src/public-api/v1/handlers/workflows/spec/paths/createWorkflow.generated.yml)
- [n8n 2.48.5 activate contract](https://github.com/n8n-io/n8n/blob/n8n%402.48.5/packages/cli/src/public-api/v1/handlers/workflows/spec/paths/activateWorkflow.generated.yml)
- [n8n 2.48.5 node sources](https://github.com/n8n-io/n8n/tree/n8n%402.48.5/packages/nodes-base/nodes)
