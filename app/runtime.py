from .entry import app
from . import poc
from .tapd_retry_patch import process_retry_jobs_v5
from .tapd_readback_patch import tapd_status_to_poc_v5, run_scheduled_tapd_sync_v5

# background_worker resolves these module globals at runtime, so replace them before lifespan starts.
poc.process_retry_jobs = process_retry_jobs_v5
poc._tapd_status_to_poc = tapd_status_to_poc_v5
poc.run_scheduled_tapd_sync = run_scheduled_tapd_sync_v5
