from contextlib import asynccontextmanager

from .entry import app
from . import poc
from .tapd_retry_patch import process_retry_jobs_v5
from .tapd_readback_patch import tapd_status_to_poc_v5, run_scheduled_tapd_sync_v5
from .budget_realdata_patch import reconcile_budget_execution_from_ledger

# background_worker resolves these module globals at runtime, so replace them before lifespan starts.
poc.process_retry_jobs = process_retry_jobs_v5
poc._tapd_status_to_poc = tapd_status_to_poc_v5
poc.run_scheduled_tapd_sync = run_scheduled_tapd_sync_v5

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def runtime_lifespan(app_instance):
    async with _original_lifespan(app_instance):
        # Database/schema initialization is complete at this point. Rebuild
        # budget execution from actual budget_transactions so old demo baseline
        # values can no longer inflate used amount or execution rate.
        reconcile_budget_execution_from_ledger()
        yield


app.router.lifespan_context = runtime_lifespan
