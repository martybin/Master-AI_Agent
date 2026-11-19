from concurrent.futures import ThreadPoolExecutor

from master_agent.compiler import compile_ir, digest
from master_agent.repository import Repository, StateConflict


def create(repo, ir, settings):
    workflow = compile_ir(ir, settings)
    return repo.create(
        prompt=None,
        ir=ir.model_dump(),
        workflow=workflow,
        sha256=digest(workflow),
        warnings=[],
        attempts=0,
    )


def test_claim_is_atomic(settings, ir):
    repo = Repository(settings.database_path)
    repo.initialize()
    record = create(repo, ir, settings)

    def claim(_):
        try:
            repo.update(record["id"], {"draft"}, "deploy_started", status="deploying")
            return True
        except StateConflict:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1


def test_restart_recovers_interrupted_mutation_as_unknown(settings, ir):
    repo = Repository(settings.database_path)
    repo.initialize()
    record = create(repo, ir, settings)
    repo.update(record["id"], {"draft"}, "deploy_started", status="deploying")
    restarted = Repository(settings.database_path)
    restarted.initialize()
    assert restarted.get(record["id"])["status"] == "deployment_unknown"
    assert restarted.events(record["id"])[-1]["action"] == "recover_interrupted"
