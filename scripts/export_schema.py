import json
from pathlib import Path

from master_agent.ir import WorkflowIR

target = Path(__file__).resolve().parents[1] / "docs/workflow-ir.schema.json"
target.write_text(json.dumps(WorkflowIR.model_json_schema(), indent=2, ensure_ascii=False) + "\n")
print(f"Saved {target}")
