from .entry import app
from . import poc
from .tapd_retry_patch import process_retry_jobs_v5

# background_worker resolves this module global at runtime, so replace it before lifespan starts.
poc.process_retry_jobs = process_retry_jobs_v5
