"""Running queued analysis runs in R (a background job whose record_ids are analysis run ids).

Each run was queued with its exact specification and data set, so the job runs what was approved and reviewed. For
individual participant data the rows are rebuilt from the encrypted files and must match the queued data set's hash.
"""

import asyncio
import logging

from sqlalchemy.orm import Session

import models
from audit import record_event
from projects_routes import ProjectAccess
from stats_engine import ANALYSIS_TYPES, StatsEngineUnavailable, StatsRunError, run_analysis
from storage import StorageError, document_storage
from synthesis_data import AnalysisSpec, DatasetError, build_dataset

logger = logging.getLogger(__name__)


def _execute(db: Session, run: models.AnalysisRun) -> None:
    analysis = run.analysis
    template, needed = ANALYSIS_TYPES[analysis.analysis_type]
    extra_files: dict[str, bytes] = {}
    if analysis.analysis_type == "ipd":
        built = build_dataset(db, run.project_id, "ipd", AnalysisSpec.model_validate(run.spec), analysis.outcome)
        if built.sha256 != run.dataset_sha256:
            raise DatasetError("The participant data changed after this run was queued. Start a new run.")
        extra_files = built.extra_files
    output = run_analysis(template, run.dataset["r_spec"], run.dataset["rows"], needed, extra_files)
    storage = document_storage()
    plots = []
    for plot in output.plots:
        key = storage.save(run.project_id, plot.content)
        plots.append(
            {
                "name": plot.name,
                "format": plot.format,
                "storage_key": key,
                "media_type": plot.media_type,
                "size_bytes": len(plot.content),
            }
        )
    run.results, run.plots, run.script = output.results, plots, output.script
    run.session_info, run.r_version, run.packages = output.session_info, output.r_version, output.packages
    run.log, run.duration_ms, run.status, run.error = output.log, output.duration_ms, "succeeded", None


async def process_analysis_runs(db: Session, access: ProjectAccess, job: models.AIJob, run_ids: list[int]) -> None:
    job.total = len(run_ids)
    db.commit()
    for run_id in run_ids:
        run = db.get(models.AnalysisRun, run_id)
        if run is None or run.project_id != job.project_id:
            job.failed += 1
            job.processed += 1
            db.commit()
            continue
        run.status = "running"
        db.commit()
        try:
            await asyncio.to_thread(_execute, db, run)
        except StatsRunError as exc:
            run.status, run.error, run.log = "failed", str(exc), exc.log
        except (StatsEngineUnavailable, DatasetError) as exc:
            run.status, run.error = "failed", str(exc)
        except StorageError:
            logger.exception("Saving plots for analysis run %s failed", run.id)
            run.status, run.error = "failed", "The plots couldn't be saved"
        if run.status == "failed":
            job.failed += 1
        run.finished_at = models.utcnow()
        job.processed += 1
        record_event(
            db,
            project_id=job.project_id,
            actor_id=access.user.id,
            action="analysis.run_finished",
            entity_type="analysis_run",
            entity_id=run.id,
            details={
                "analysis_id": run.analysis_id,
                "status": run.status,
                "is_final": run.is_final,
                "spec_sha256": run.spec_sha256,
                "dataset_sha256": run.dataset_sha256,
                "r_version": run.r_version,
                "error": run.error,
            },
        )
        db.commit()
