import test from "node:test";
import assert from "node:assert/strict";
import {
  EventParser,
  errorMessage,
  nodeDetail,
  statuses,
} from "../src/master_agent/web/ui-utils.mjs";

test("SSE parser handles Persian text split across arbitrary chunks and comments", () => {
  const events = [];
  const parser = new EventParser((event) => events.push(event));
  const source =
    ': keep-alive\n\ndata: {"type":"stage","stage":"thinking"}\n\ndata: {"type":"complete","message":"سلام دنیا"}\n\n';
  for (let i = 0; i < source.length; i += 3)
    parser.push(source.slice(i, i + 3));
  assert.deepEqual(events, [
    { type: "stage", stage: "thinking" },
    { type: "complete", message: "سلام دنیا" },
  ]);
});

test("SSE parser accepts CRLF across read boundaries and rejects invalid JSON", () => {
  const events = [];
  const parser = new EventParser((event) => events.push(event));
  parser.push('data: {"type":"done"}\r');
  parser.push("\n\r");
  parser.push("\n");
  assert.deepEqual(events, [{ type: "done" }]);
  assert.throws(() => parser.push("data: nope\n\n"));
});

test("SSE parser respects UTF-8 byte boundaries through streaming decoder", () => {
  const events = [];
  const parser = new EventParser((event) => events.push(event));
  const decoder = new TextDecoder();
  const bytes = new TextEncoder().encode(
    'data: {"message":"ایجنت آماده است"}\n\n',
  );
  for (const byte of bytes)
    parser.push(decoder.decode(Uint8Array.of(byte), { stream: true }));
  parser.push(decoder.decode());
  assert.equal(events[0].message, "ایجنت آماده است");
});

test("connection errors never expose raw upstream bodies", () => {
  const text = errorMessage({
    status: 503,
    detail: { message: "PRIVATE_API_KEY" },
  });
  assert(!text.includes("PRIVATE_API_KEY"));
  assert.match(errorMessage({ status: 401 }), /workspace key/);
});

test("workflow labels preserve uncertain and inactive states", () => {
  assert.match(statuses.deployed, /inactive/);
  assert.match(statuses.deployment_unknown, /Reconciliation/);
  assert.equal(
    nodeDetail({ kind: "ollama_chat", input_path: "body.message" }),
    "Input: body.message",
  );
});
