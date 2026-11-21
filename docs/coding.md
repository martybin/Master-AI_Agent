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
unchecked confirmation. **اجازه و ادامه** approves through an authenticated
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
folder must already exist. Grants can be revoked even after a folder disappears.

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
  against a current read. It accepts `revision` or `expected_sha256`; if omitted,
  the controller binds the last actual read of that path in this chat. Without
  a read, existing files cannot be overwritten. Stale content still fails closed.
- `code.replace`: one unique exact old snippet, replacement and read revision.
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
