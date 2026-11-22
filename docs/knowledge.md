# Official documentation retrieval

BisCode retrieves public documentation **before generating agent implementations**.
This augments the local Ollama model; it does not train or replace it.

- **Python agent mode:** LangChain, LangGraph and LlamaIndex documentation.
  New projects start with a real LangGraph `StateGraph` and declare their runtime
  dependency in `requirements.txt`. Extend the graph for the requested features;
  use LangChain integrations or LlamaIndex retrieval where appropriate. These
  libraries are not forced together into every generated project.
- **n8n mode:** n8n concepts and node documentation. The installed node catalog,
  IR schema, validator and owner approval remain authoritative. Current upstream
  docs may describe features absent from an older self-hosted instance.
- **General local coding mode:** no automatic documentation context and no
  `docs.search` model tool. The existing coding model and execution tools continue
  to operate normally. Switch to Python agent mode for framework-based agents.

## Update and inspect

In **Getting started → Official documentation · RAG**, inspect indexed sources
and dates or update the official documentation. This requires the owner key.
Updates fetch only the curated public URLs in
[`sources.py`](../src/master_agent/knowledge/sources.py). This is a selected
starter corpus, not a mirror of the entire documentation sites.

```bash
uv run biscode docs sync
uv run biscode docs sync --family n8n
uv run biscode docs status
uv run biscode docs search --target python --query 'ایجنت با حافظه و اسناد'
uv run biscode docs search --target n8n --query 'Ollama agent webhook'
```

The corpus is stored in `knowledge.sqlite3` beside the configured application
SQLite database. `KNOWLEDGE_PATH` overrides that path; `DOCS_ENABLED=false`
disables retrieval in the server. There are no automatic startup downloads.
Once synced, retrieval works offline. CLI synchronization is an explicit owner
maintenance action; use `docs status` to check readiness.

Owner REST endpoints:

- `GET /api/v1/knowledge/status`
- `GET /api/v1/knowledge/search?target=python&query=agent&limit=4`
- `POST /api/v1/knowledge/sync?family=n8n` (omit family for all sources)

The model can search the already downloaded corpus with `docs.search` in agent
building modes; it cannot synchronize or supply arbitrary URLs through that tool.
Automatically retrieved source URLs, content hashes and sync timestamps are saved
on the assistant turn. The chat displays **Retrieved official references**.
These are retrieved candidates, not proof that every cited passage was used or
that the resulting implementation is correct.

## Retrieval and boundaries

The initial backend is SQLite **FTS5/BM25**, a lexical RAG implementation with
bounded overlapping chunks, English identifier search and a small Persian term
expansion table. It requires no embedding model, vector service, paid API key,
or extra GPU memory. Retrieval quality for paraphrases is more limited than
multilingual semantic retrieval; it is not a semantic embedding index. Up to
four source pages enter the automatic context (roughly 5 KB of text plus metadata).

Sync streams at most 1 MB per page and checks each redirect against the fixed
HTTPS official-domain allowlist. HTML script/navigation markup is discarded.
A page replaces its previous chunks atomically only after a successful fetch;
failures keep the last good copy and are reported. Re-sync is idempotent. No
prompts, private project files, credentials or chat history are uploaded to the
documentation sites; only the fixed public URLs are downloaded.

Documentation is **reference data, never executable instructions or permission**.
Retrieved text cannot grant file/system access or activate n8n workflows. No
framework package is installed into an owner project merely by indexing docs.
Install each generated project's declared dependencies in its own environment
before running it. Generated unit tests mock Ollama and do not verify a live
provider connection or all custom integration behavior.

Official starting points:

- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LlamaIndex framework](https://developers.llamaindex.ai/python/framework/)
- [n8n AI documentation](https://docs.n8n.io/build/integrate-ai.md)
