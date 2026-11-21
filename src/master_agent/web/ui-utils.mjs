export const statuses = {
  draft: "Draft",
  deploying: "Deploying",
  deployed: "Deployed · inactive",
  activating: "Activating",
  active: "Active",
  deactivating: "Deactivating",
  deployment_unknown: "Reconciliation required",
  activation_unknown: "Reconciliation required",
};
export const nodeKinds = {
  manual: ["Manual trigger", "play"],
  webhook: ["Webhook", "webhook"],
  schedule: ["Schedule", "clock"],
  set: ["Set fields", "sliders"],
  http_request: ["HTTP request", "globe"],
  ollama_chat: ["Local AI", "spark"],
  if_equals: ["Condition", "branch"],
  respond: ["Respond", "check"],
};
export const stageLabels = {
  checking_tools: "Checking results…",
  tool_running: "Running a local tool…",
  thinking: "Thinking through your request…",
  planning: "Planning the workflow with your local model…",
  validating: "Validating nodes and connections…",
  compiling: "Compiling the workflow for n8n…",
};
export const warningLabels = {
  telegram_bridge:
    "After review and activation, bind this workflow to a Telegram connection in Local Agent.",
  native_workflow:
    "Review and test the nodes, code, and expressions in n8n. Structural validation does not guarantee correct execution.",
  webhook_public:
    "This webhook has no authentication. The supplied configuration is intended for local use.",
  ollama_result: "The model response is available at message.content.",
  external_http:
    "This workflow can send data to the HTTP destinations shown here.",
};
export function errorMessage(error) {
  if (error.status === 401)
    return "The workspace key is invalid or the session has expired.";
  if (error.status === 429)
    return "The model is busy. Try again after the current request finishes.";
  if (error.status === 409)
    return "The state or version has changed. Refresh and review it again.";
  if (error.status === 422)
    return "The request is invalid. Check the required fields and describe the input, task, and expected output.";
  if (error.status === 413)
    return "The message is too long. Please shorten it.";
  if (error.detail?.code === "not_configured")
    return "The n8n API key has not been configured.";
  if (error.status === 503)
    return "The service is unavailable. Check Connections; the operation will not be retried automatically.";
  return "The connection was interrupted. Check the chat status and your connection to BisCode.";
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
  if (node.kind === "native") return node.node_type;
  if (node.kind === "webhook") return `${node.method} / ${node.path}`;
  if (node.kind === "schedule") return `Every ${node.every_minutes} minutes`;
  if (node.kind === "ollama_chat") return `Input: ${node.input_path}`;
  if (node.kind === "if_equals") return `${node.input_path} = ${node.equals}`;
  if (node.kind === "http_request") return `${node.method} ${node.url}`;
  if (node.kind === "set")
    return node.assignments.map((item) => item.name).join(", ");
  if (node.kind === "respond") return `JSON · ${node.status_code}`;
  return "Run from the n8n editor";
}
