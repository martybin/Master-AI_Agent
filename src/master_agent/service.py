from master_agent.clients import IntegrationError, N8nClient
from master_agent.compiler import create_payload, digest
from master_agent.repository import Repository, StateConflict


def remote_snapshot(remote: dict) -> dict:
    return {
        key: remote.get(key)
        for key in (
            "id",
            "name",
            "nodes",
            "connections",
            "settings",
            "versionId",
            "active",
            "activeVersionId",
        )
    }


def verify_remote(record: dict, remote: dict) -> None:
    """Fail closed on executable changes, including node flags or added credentials."""
    if record.get("n8n_workflow_id") and remote.get("id") != record["n8n_workflow_id"]:
        raise StateConflict("n8n returned a different workflow id")
    expected = create_payload(record["workflow"])
    if (
        remote.get("name") != expected["name"]
        or remote.get("connections") != expected["connections"]
    ):
        raise StateConflict("Remote workflow differs from the compiled draft")
    if not isinstance(remote.get("settings"), dict):
        raise StateConflict("n8n returned malformed workflow settings")
    actual_settings = dict(remote["settings"])
    # n8n derives these two fields server-side. Only the v1 engine is supported here.
    if actual_settings.get("binaryMode") in {"separate", "combined"}:
        actual_settings.pop("binaryMode")
    if actual_settings.get("engineType") in {None, "v1"}:
        actual_settings.pop("engineType", None)
    if actual_settings != expected["settings"]:
        raise StateConflict("Remote workflow settings differ from the compiled draft")
    actual_nodes = remote.get("nodes", [])
    if (
        not isinstance(actual_nodes, list)
        or len(actual_nodes) != len(expected["nodes"])
        or any(not isinstance(node, dict) for node in actual_nodes)
    ):
        raise StateConflict("Remote node list changed")
    by_id = {node.get("id"): node for node in actual_nodes}
    allowed_visual = {"position", "notes", "notesInFlow", "color", "createdAt", "updatedAt"}
    safe_defaults = {
        "disabled": False,
        "executeOnce": False,
        "alwaysOutputData": False,
        "retryOnFail": False,
        "continueOnFail": False,
        "onError": "stopWorkflow",
        "credentials": {},
    }
    for node in expected["nodes"]:
        actual = by_id.get(node["id"], {})
        for key, value in node.items():
            if key not in allowed_visual and actual.get(key) != value:
                raise StateConflict("Remote node parameters differ from the compiled draft")
        for key, value in actual.items():
            if key in node or key in allowed_visual:
                continue
            if key not in safe_defaults or value != safe_defaults[key]:
                raise StateConflict("Remote workflow contains unreviewed executable node options")


class BuildService:
    def __init__(self, repo: Repository, n8n: N8nClient):
        self.repo, self.n8n = repo, n8n

    async def deploy(self, build_id: str) -> dict:
        record = self.repo.get(build_id)
        if record["status"] in {"deployed", "active"}:
            return record
        # Validate configuration before claiming a mutation with potentially unknown outcome.
        if not self.n8n.settings.n8n_api_key.get_secret_value():
            raise IntegrationError("n8n", "not_configured", "Set N8N_API_KEY before deploying")
        self.repo.update(build_id, {"draft"}, "deploy_started", status="deploying")
        try:
            remote = await self.n8n.create_workflow(record["workflow"])
        except IntegrationError as exc:
            self.repo.update(
                build_id,
                {"deploying"},
                "deploy_uncertain",
                status="deployment_unknown",
                last_error=exc.code,
            )
            raise
        return self.repo.update(
            build_id,
            {"deploying"},
            "deployed",
            status="deployed",
            n8n_workflow_id=remote["id"],
            last_error=None,
        )

    async def review(self, build_id: str) -> dict:
        record = self.repo.get(build_id)
        if record["status"] != "deployed":
            raise StateConflict("Only an inactive deployed workflow can be reviewed")
        remote = await self.n8n.get_workflow(record["n8n_workflow_id"])
        verify_remote(record, remote)
        if (
            remote.get("active") is not False
            or remote.get("activeVersionId") is not None
            or not isinstance(remote.get("versionId"), str)
            or not remote["versionId"]
        ):
            raise StateConflict("n8n must report an inactive workflow with a versionId")
        snapshot = remote_snapshot(remote)
        review_hash = digest(snapshot)
        self.repo.update(build_id, {"deployed"}, "activation_review", review_sha256=review_hash)
        return {
            "build_id": build_id,
            "workflow_sha256": record["workflow_sha256"],
            "review_sha256": review_hash,
            "remote_workflow": snapshot,
            "warnings": record["warnings"],
            "requires_explicit_confirmation": True,
        }

    async def activate(
        self, build_id: str, *, confirmed: bool, workflow_sha256: str, review_sha256: str
    ) -> dict:
        if confirmed is not True:
            raise StateConflict("Explicit human confirmation is required")
        record = self.repo.get(build_id)
        if workflow_sha256 != record["workflow_sha256"]:
            raise StateConflict("Compiled workflow fingerprint mismatch")
        self.repo.update(
            build_id,
            {"deployed"},
            "activation_confirmed",
            review_guard=review_sha256,
            status="activating",
        )
        try:
            remote = await self.n8n.get_workflow(record["n8n_workflow_id"])
            verify_remote(record, remote)
            if (
                remote.get("active") is not False
                or remote.get("activeVersionId") is not None
                or digest(remote_snapshot(remote)) != review_sha256
            ):
                raise StateConflict("Remote workflow changed since review; review again")
        except (IntegrationError, StateConflict):
            self.repo.update(
                build_id,
                {"activating"},
                "activation_review_invalid",
                status="deployed",
                review_sha256=None,
            )
            raise
        try:
            activated = await self.n8n.activate(
                record["n8n_workflow_id"], confirmed=True, version_id=remote["versionId"]
            )
            if (
                activated.get("active") is not True
                or "activeVersionId" in activated
                and activated["activeVersionId"] != remote["versionId"]
            ):
                raise IntegrationError(
                    "n8n", "unexpected_activate", "n8n did not confirm activation"
                )
        except IntegrationError as exc:
            self.repo.update(
                build_id,
                {"activating"},
                "activation_uncertain",
                status="activation_unknown",
                last_error=exc.code,
                review_sha256=None,
            )
            raise
        return self.repo.update(
            build_id,
            {"activating"},
            "activated",
            status="active",
            review_sha256=None,
            last_error=None,
        )

    async def deactivate(self, build_id: str) -> dict:
        record = self.repo.get(build_id)
        self.repo.update(build_id, {"active"}, "deactivation_started", status="deactivating")
        try:
            remote = await self.n8n.deactivate(record["n8n_workflow_id"])
            if remote.get("active") is not False:
                raise IntegrationError(
                    "n8n", "unexpected_deactivate", "n8n did not confirm deactivation"
                )
        except IntegrationError as exc:
            self.repo.update(
                build_id,
                {"deactivating"},
                "deactivation_uncertain",
                status="activation_unknown",
                last_error=exc.code,
            )
            raise
        return self.repo.update(
            build_id,
            {"deactivating"},
            "deactivated",
            status="deployed",
            review_sha256=None,
            last_error=None,
        )

    async def reconcile(self, build_id: str, workflow_id: str | None) -> dict:
        record = self.repo.get(build_id)
        status = record["status"]
        if status not in {"deployment_unknown", "activation_unknown"}:
            raise StateConflict("Reconciliation is only available for uncertain mutations")
        remote_id = record["n8n_workflow_id"] or workflow_id
        if not remote_id:
            raise StateConflict("Find the created workflow in n8n and supply its workflow_id")
        remote = await self.n8n.get_workflow(remote_id)
        verify_remote(record, remote)
        if type(remote.get("active")) is not bool:
            raise StateConflict("n8n did not report workflow activation state")
        if (
            remote["active"]
            and "activeVersionId" in remote
            and remote["activeVersionId"] != remote.get("versionId")
        ):
            raise StateConflict("Published version differs from the current draft; inspect n8n")
        return self.repo.update(
            build_id,
            {status},
            "reconciled",
            status="active" if remote["active"] else "deployed",
            n8n_workflow_id=remote_id,
            review_sha256=None,
            last_error=None,
        )
