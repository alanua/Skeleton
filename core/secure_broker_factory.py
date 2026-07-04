from __future__ import annotations

from core.isolated_process_executor import isolated_local_process_executor
from core.runner_execution_broker import (
    RunnerExecutionBroker,
    composite_executor_factory,
    network_http_executor,
)
from core.runner_executors import (
    ExecutionContext,
    filesystem_executor,
    python_entrypoint_executor,
    remote_ssh_executor,
    repository_executor,
)


def secure_default_broker(context: ExecutionContext) -> RunnerExecutionBroker:
    broker = RunnerExecutionBroker(
        {
            "local.process": isolated_local_process_executor,
            "remote.ssh": remote_ssh_executor(context),
            "network.http": network_http_executor,
            "python.entrypoint": python_entrypoint_executor(context),
            "filesystem": filesystem_executor(context),
            "repository": repository_executor(context),
        }
    )
    broker._executors["composite"] = composite_executor_factory(broker)
    return broker
