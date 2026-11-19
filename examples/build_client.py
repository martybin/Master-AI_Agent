"""Build from a prompt or a local IR file; optional deployment is always inactive."""

import argparse
import json
from pathlib import Path

import httpx

from master_agent.config import Settings

parser = argparse.ArgumentParser(description=__doc__)
source = parser.add_mutually_exclusive_group(required=True)
source.add_argument("--prompt", help="Describe the desired workflow")
source.add_argument("--ir", type=Path, help="Compile an existing IR JSON file without the model")
parser.add_argument("--deploy", action="store_true", help="Create the workflow in n8n, inactive")
args = parser.parse_args()
settings = Settings()
headers = {"Authorization": "Bearer " + settings.master_api_key.get_secret_value()}
with httpx.Client(
    base_url="http://127.0.0.1:8000",
    headers=headers,
    trust_env=False,
    timeout=settings.ollama_timeout_seconds * settings.planner_max_attempts + 30,
) as client:
    if args.ir:
        response = client.post("/api/v1/builds/from-ir", json=json.loads(args.ir.read_text()))
    else:
        response = client.post("/api/v1/builds", json={"prompt": args.prompt})
    if response.is_error:
        raise SystemExit(f"Build failed ({response.status_code}): {response.text}")
    record = response.json()
    if args.deploy:
        response = client.post(f"/api/v1/builds/{record['id']}/deploy")
        if response.is_error:
            raise SystemExit(f"Deployment failed ({response.status_code}): {response.text}")
        record = response.json()
folder = Path("outputs")
folder.mkdir(exist_ok=True)
path = folder / f"build-{record['id']}.json"
path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
workflow_path = folder / f"workflow-{record['id']}.json"
workflow_path.write_text(json.dumps(record["workflow"], ensure_ascii=False, indent=2))
print(f"Build: {record['id']}\nStatus: {record['status']}\nSaved: {path}\nImport: {workflow_path}")
print("Workflow activation requires a separate, explicit review and approval.")
