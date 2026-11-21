# BisCode local coding contract

BisCode 0.4 adds a coding path alongside the existing n8n builder. It uses the
configured Ollama model, including Qwen 3.5 9B; it does not require a cloud API
or invoke Codex/Claude Code internally. It supplies a local coding tool loop,
not a promise of the same planning quality as those products.

## Choosing the output

Chats store `target`: `auto`, `n8n`, `python` or `code`. The UI's method selector
updates that owner setting while idle. Auto recognizes explicit Python/coding
requests; n8n remains the default. Explicit selection wins. Python/code routing
excludes n8n tools and the small-IR build action. Existing chats default to auto;
existing build/chat data and activation approval contracts remain compatible.

The coding prompt uses a smaller relevant tool manifest. It can inspect/search
sources, create files, edit exact snippets, inspect changes, run subprocesses,
read the results and fix errors. The independent completion review remains in
place. A generated project cannot be declared checked without a `project.check`
run for the current tracked files. Work is bounded by `CODING_MAX_STEPS` (20 by
default); n8n retains `LOCAL_MAX_STEPS`. Model mistakes and context limits still
apply. Passing tests does not establish all requested semantics.

## Permission, approval and continuation

A coding action with no grant creates a pending request and pauses before file
changes. The model may also call `access.request`, or a denied file/command tool
can create a request for the missing scope. None of these grant permission.

The owner sees reason, editable workspace, level, four-hour duration and an
unchecked confirmation. **Approve & continue** approves through an authenticated
owner API and resumes the same original task. The owner can grant only workspace
access; executing tests/commands then requires a separate system grant. Rejecting
does not run the task. Old requests cannot authorize a newer task or a changed
build target. Replaying a successful resume is idempotent and returns the saved
turn rather than calling the model again. Expired/revoked grants cannot resume.

`workspace` limits file tools to the resolved chosen folder and rejects symlink
escapes. `system` adds commands and broader filesystem reach with the current
OS user's permissions. The selected workspace is the working directory, not an
OS isolation boundary. Permission is per chat; changing one chat's directory
does not mutate the global workspace or another chat's grant. The selected
folder must exist when access is granted. Use **Browse** to choose an existing
folder anywhere your OS user can access, or **New folder** in the picker to
create one before approving. The native picker uses Zenity directory mode when
available; the in-app browser works without it. Browsing/selection never grants
access automatically. Grants can be revoked even after a folder disappears.

The model cannot approve requests through a published tool. This is a local,
single-owner application: the owner bearer key is administrative, and arbitrary
system commands can access OS-user files and make requests themselves. Do not
treat normal UI approval as a sandbox against code with full user privileges,
or expose the owner key through a model HTTP connector. No automatic root,
Docker socket mount or privilege escalation is provided.

## Code tools

- `project.inspect`: inspect an allowed folder and project metadata.
- `files.search`: bounded literal source search with paths and line numbers;
  skips symlinks, private/generated files and files over 100 KB.
- `files.read`: returns SHA256 of actual bytes and a short, chat/path-scoped revision.
- `code.write_file`: create a new source file, or replace an existing file only
  against a known content snapshot. It accepts `revision` or `expected_sha256`; if
  omitted, the controller binds the last read or successful write of that path in
  this chat. Unseen existing files cannot be overwritten. Stale content still fails closed.
- `code.replace`: one unique exact old snippet, replacement and a revision from a read or successful write.
  Stale hashes, ambiguous matches and existing large files are rejected.
- `code.changes`: actual changes recorded by these coding tools, as unified
  diffs. Before/after snapshots are Fernet-encrypted in `code_changes`; API
  responses omit ciphertext and redact known secrets.
- `project.create_python_agent`: create a standalone dependency-free Python
  project in a new relative directory. Existing directories are never overwritten.
  An identical retry in the same chat reuses its existing project, preserving edits.
- `project.check`: execute `python -m unittest discover -s tests -v` in the real
  project directory. System permission is mandatory because tests execute code.
- `terminal.exec`: the existing bounded general command tool can run other
  checks, install required dependencies or perform requested system work.

Coding mode uses the hash-checked writer instead of publishing legacy
`files.write` to the model. Normal n8n tooling and direct owner APIs stay
compatible. Changes made by arbitrary terminal commands or other programs are
not automatically captured as managed diffs; use version control for complete
repository history. Hash checks prevent ordinary stale edits; they do not lock
out unrelated OS processes during the filesystem/SQLite operations.

The Python base contains `agent.py`, `agent.json`, a standard-library unittest
suite, README and gitignore. It has an Ollama chat client, CLI and local HTTP
`POST /chat` endpoint with optional `AGENT_API_KEY`. It is initially a stateless
chat agent; the coding loop must implement additional requested integrations,
scheduling, memory or other behavior before claiming them complete. Secrets
are not copied from BisCode into generated code. Building/checking does not
start the child service, send Telegram messages or deploy/activate n8n.

Runs persist real stdout/stderr, exit code, timeout, test count and a fingerprint
of tracked project files. An empty unittest suite is not marked passed. Changes
after a check make its result stale in the UI. Interrupted runs become
`interrupted` on restart; they are not silently rerun. Cancellation uses the
existing terminal process-group cleanup on POSIX and records cancellation.
Stopping cannot undo side effects already completed by arbitrary code.

The Projects panel shows recorded files, checks, diffs and an authenticated ZIP
download. Archives contain tracked files only, enforce containment/size limits
and skip private files. They do not include virtualenvs, an untracked `.env`,
BisCode's owner key, vault key or database. Back up the SQLite database and
`vault.key` together: encrypted code history now also requires that original key.

## Owner API additions

All routes require the existing owner Bearer key:

- `POST /api/v1/chats` accepts optional `{target}`; an empty body is compatible.
- `PUT /api/v1/chats/{id}/target`: `{target}` while the chat is idle.
- `GET /api/v1/local/access-requests/{id}`: the current request record.
- `POST .../access-requests/{id}/approve`: `{confirm:true, mode, workspace, hours}`.
- `POST .../access-requests/{id}/reject`: refuse a pending request.
- `POST /api/v1/chats/{id}/resume`: `{access_request_id}`; approved task only,
  streaming the same SSE stages as a normal message, or JSON for replay.
- `GET /api/v1/local/projects?chat_id=...`: project metadata and latest real run.
- `GET /api/v1/local/projects/{id}/archive`: authenticated ZIP.
- `GET /api/v1/local/code-changes?chat_id=...`: recorded diffs.

Messages can include `project_id` and `access_request`. A `permission_required`
SSE event accompanies a completed clarification/approval request; it is not a
successful code execution. `project` metadata can accompany a completed turn.

## Run and verify

```bash
uv sync --frozen --group dev
uv run biscode serve --workspace /absolute/path/to/project
# master-local remains a compatible alias
make test
make test-ui
make check
make smoke-coding
```

The live coding smoke uses the actual configured Ollama model in a temporary
workspace/database: permission request, owner approval, project generation,
actual subprocess tests, an existing-file edit and rerun. It checks actual
files, AST, diffs and recorded runs rather than trusting a success sentence.
It never activates a workflow, starts a permanent child agent service or uses
real Telegram credentials. Unit/integration tests also cover strict approval,
request staleness/replay, path scope, edit conflicts, encrypted history, archive
containment, stale/empty checks and cancellation. Keep one BisCode process and
one worker per database.

## When a coding request appears to be waiting

Before its first file operation, BisCode records an access request and opens the
workspace approval dialog. Choose a folder, review the scope, select the explicit
confirmation, and click **Approve & continue**. The saved request resumes after
approval. Closing the page preserves a pending request; it is shown again when
you reconnect. Approved requests are not automatically reapproved. Tests require
system access.

The interface checks the durable message state alongside live events. If the
final SSE event is lost, it recovers the saved response. **Local coding** uses file
editing and checks for scripts and libraries. **Python agent** exposes the
starting Ollama agent template.

If a desktop launch cannot find the plain `python` or `python3` executable on PATH,
that alias falls back to BisCode's own Python interpreter. Explicit interpreter
paths and other executables are preserved. `system.inspect` exposes the actual
`python_executable` path.

A read or successful write records a content hash and revision. Follow-up edits
can use that known snapshot without rereading unchanged files. Outside edits still
fail the hash guard, and an explicitly stale revision remains invalid.

Coding decisions use a JSON Schema that binds each tool name to its argument
shape. The small model's manifest hides optional manual hash arguments; the
controller applies the same optimistic guard using real reads and writes. The
direct API still accepts explicit `revision` and `expected_sha256` values.

For ordinary Python code, `code.check` takes the source folder containing
`tests/`. The controller selects the interpreter, command, and working directory,
then saves the actual result and test count in `code_runs`. Zero tests, timeouts,
or source changes during a check cannot pass. This tool requires system access.
If planning fails after a write, the response includes the actual changed paths
and available test status.

## Interface language and Persian support

The interface can be switched between English and Persian in the top bar.
Persian conversation text, model
instructions, request detection, and UTF-8 input remain supported. Message bodies
and the composer use automatic text direction, while code blocks stay LTR.
Changing the interface language does not replace the model or rewrite saved chats.

Owner-only folder endpoints (not available as model tools):

- `GET /api/v1/local/browse?path=...&hidden=false`: bounded child directory listing;
  omitted path opens the host user's home. Returned entries contain absolute paths.
- `POST /api/v1/local/pick-folder?path=...`: native directory chooser, returning
  `path` on selection, `cancelled:true` on cancel, or `available:false` for fallback.
- `POST /api/v1/local/folders`: `{parent, name}` creates one new directory under
  an existing parent. Rejects path separators, traversal, and existing names.

All require the owner key. They do not modify chat grants or approve requests.
