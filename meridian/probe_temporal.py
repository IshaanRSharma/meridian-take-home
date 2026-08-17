"""Throwaway probe: can a Temporal test environment start in this repo, offline?"""

import asyncio

import temporalio
from temporalio.testing import WorkflowEnvironment


async def main() -> None:
    """Report the SDK version and whether a time-skipping environment starts."""
    print("temporalio", temporalio.__version__)  # noqa: T201
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            print("time-skipping OK", env.client.namespace)  # noqa: T201
    except Exception as error:  # noqa: BLE001
        print("time-skipping FAILED", type(error).__name__, error)  # noqa: T201


asyncio.run(main())
