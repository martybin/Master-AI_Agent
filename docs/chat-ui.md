# BisCode chat workspace

The bilingual English/Persian interface is served by the same FastAPI process as the API, at
`http://127.0.0.1:8000/` by default. It uses bundled HTML, CSS, JavaScript, icons,
and fonts. No separate frontend service, Node.js runtime, or online font download
is required.

Persian input and model instructions are preserved. The interface is LTR, while
message text and the composer choose their direction automatically. Persian
numbered lists remain supported by the Markdown renderer, and code blocks remain LTR.

## Connect and start a conversation

```bash
make run
```

1. Open the workspace and select **Connect workspace**.
2. Enter `MASTER_API_KEY` from `.env`. This is different from `N8N_API_KEY`.
3. By default, the key stays in page memory. **Keep me connected until this tab
   closes** stores it in that tab's `sessionStorage`. It is not embedded in the
   page, URL, or `localStorage`.
4. Choose a build method, describe the task in English or Persian, and send it.
   Enter sends a message; Shift+Enter inserts a newline. Ctrl/Cmd+K starts a new chat.

The model keeps its Persian-speaking instructions; translating the interface
does not switch the configured model or remove Persian capability.

## Build methods

- **Auto:** n8n is the default; explicit Python/coding requests can use local coding.
- **n8n workflow:** generate and refine an n8n workflow.
- **Python agent:** create and customize a Python agent project.
- **Local coding:** work on scripts, libraries, or an existing codebase.

Change the build method while the chat is idle. Local coding requests open an
access dialog before using the filesystem. Choose the folder and scope, confirm,
and select **Approve & continue**. See [the coding contract](coding.md).

## Workflows and activation

The workflow inspector shows real nodes, connections, and compiled JSON. **My
workflows** lists both API-created and chat-created builds. You can download a
workflow JSON artifact for manual import without activating it.

After n8n is configured, **Deploy to n8n** creates an inactive workflow. **Review &
activate** opens a separate dialog showing the exact version and request
destinations. The owner must select the approval checkbox and press **Approve &
activate**. The server rechecks fingerprints and the n8n version.

An uncertain deployment result offers reconciliation rather than automatic
redeployment. Reconciliation reads the remote state. A chat message asking for
activation does not expose an activation tool to the model.

**Projects & code** shows Python agent project files, actual test output, managed
diffs, and authenticated ZIP downloads. Project creation does not start its service.

## History and persistence

Chats and messages are stored in SQLite, independently of the browser session.
Refreshing the page, closing a tab, or restarting the application does not delete
them. Reconnect to the same workspace and database to see the saved history.

- Pin and unpin chats with the pin icon. Pins persist on the server and appear first.
- Search includes pinned and unpinned chats.
- The trash icon opens a confirmation dialog. Deleting a chat removes its messages,
  local grant, pending access requests, and revision handles. Existing projects,
  files, workflow builds, and execution records are retained.
- A busy chat must be stopped before deletion.

The last selected chat ID is saved in `localStorage`; message text and owner keys
are not. After reconnecting, the interface restores that chat if it exists, or
opens the first chat in the list. Legacy empty-chat titles are presented as
**New chat** without rewriting stored user content.

The API currently returns all saved chats and messages. Large histories may take
longer to load. Model context remains separately bounded to recent content.

## Streaming, recovery, and cancellation

Stage events represent real server activity. Coding steps show the current tool
and step number; elapsed time is measured locally. No synthetic percentage is shown.
The UI also checks durable message state to recover a final response if the SSE
connection misses a completion event.

Closing the browser connection does not cancel the backend task. Reopen the chat
to see its result. **Stop** cancels the task and its current local subprocess.
Stopping cannot undo operations that have already completed.

If the application restarts during a task, its pending assistant message is marked
interrupted. Requests have idempotency IDs: replaying the same ID and content
returns the saved turn, while reusing the ID for different content returns 409.

The direct-build and chat paths share an inference lock. Run one process and one
worker for each database.

## Owner API endpoints

All these routes require the owner's Bearer key:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/ui/config` | Model name, n8n editor address, allowed hosts, and version; no secrets |
| `GET /api/v1/chats` | Saved chats, pinned first and then ordered by recency |
| `POST /api/v1/chats` | Create a chat with an optional `target` |
| `GET /api/v1/chats/{id}` | Saved messages in send order |
| `PUT /api/v1/chats/{id}/target` | Change the build method while idle |
| `PUT /api/v1/chats/{id}/pin` | Set a strict boolean `pinned` value |
| `DELETE /api/v1/chats/{id}` | Delete an idle chat and revoke its local access |
| `POST /api/v1/chats/{id}/messages` | Send `content` and a UUID `request_id`; returns SSE or a saved replay |
| `POST /api/v1/chats/{id}/resume` | Resume a saved, owner-approved access request |
| `POST /api/v1/chats/{id}/cancel` | Stop the current task |
| `GET /api/v1/builds` | Recent workflow build summaries |

Messages are rendered as text and a limited Markdown subset. User/model HTML is
not executed. The workspace uses a single-owner authentication model, not a
multi-user account system.

## Verification

```bash
make test
make test-ui
make check
```

The regression tests cover conversation persistence, replay, interrupted turns,
permission approval, pins, deletion, activation guards, safe rendering contracts,
and Persian UTF-8 event parsing. Live model smoke tests are opt-in and do not
establish that a generated workflow runs successfully in n8n.

## Language and project folders

Use the language selector in the top bar to switch instantly between **English**
and **فارسی**. This browser remembers the choice across reloads. Persian uses an
RTL shell; chat text follows its own direction, while code and paths remain LTR.
Switching language does not change the model, prompts, saved chat content, or
permission checkboxes. Unknown backend/plugin error details remain verbatim.

In the local access dialog or Local Agent → Access, select **Browse**. On the
host desktop, BisCode opens Zenity's native directory chooser when available;
without a usable desktop chooser, the in-app directory browser remains available.
Use **Home**, **Up**, **Show hidden folders**, and **New folder** to reach or create
a project folder without typing its absolute path. Select **Use this folder**,
then review and explicitly approve access. Browsing and selecting a folder do
not grant model permissions. Creating a folder creates only that empty directory.
The picker browses the machine running the backend, so run BisCode on the same
computer where you want to code. Each chat can use a different folder.
