"""Interactive owner approval. Do not invoke this script from an LLM or unattended job."""

import argparse
import json
from pathlib import Path

import httpx

from master_agent.config import Settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("build_id")
args = parser.parse_args()
settings = Settings()
headers = {"Authorization": "Bearer " + settings.master_api_key.get_secret_value()}
with httpx.Client(
    base_url="http://127.0.0.1:8000", headers=headers, trust_env=False, timeout=30
) as client:
    response = client.post(f"/api/v1/builds/{args.build_id}/activation-review")
    response.raise_for_status()
    review = response.json()
    remote = review["remote_workflow"]
    folder = Path("outputs")
    folder.mkdir(exist_ok=True)
    path = folder / f"activation-review-{args.build_id}.json"
    path.write_text(json.dumps(review, indent=2, ensure_ascii=False))
    print(f"Review saved: {path.resolve()}")
    print(f"n8n editor: {settings.n8n_base_url}/workflow/{remote['id']}")
    print(f"Workflow: {remote['name']}\nVersion: {remote['versionId']}")
    for issue in review["warnings"]:
        print(f"Review note: {issue['message']}")
    phrase = f"ACTIVATE {remote['id']}"
    print("Read the complete saved review, including nodes, destinations and connections.")
    try:
        answer = input(f"To approve this exact version, type {phrase!r}: ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nNo approval received. Nothing activated.") from None
    if answer != phrase:
        raise SystemExit("No approval received. Nothing activated.")
    result = client.post(
        f"/api/v1/builds/{args.build_id}/activate",
        json={
            "confirm": True,
            "workflow_sha256": review["workflow_sha256"],
            "review_sha256": review["review_sha256"],
        },
    )
    result.raise_for_status()
    print(f"Status: {result.json()['status']}")
