import atexit

try:
    from ._version import __version__
except ImportError:
    # Fallback when using the package in dev mode without installing
    # in editable mode with pip. It is highly recommended to install
    # the package from a stable release or in editable mode: https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs
    import warnings
    warnings.warn("Importing 'myextension' outside a proper installation.")
    __version__ = "dev"
from .routes import setup_route_handlers
from .analysis_job_store import AnalysisJobStore
from .analysis_worker import AnalysisWorker
from .behavior_log_store import resolve_log_root
from .classroom_brief_automation import ClassroomBriefRefresher
from .evidence_outbox import EvidenceOutbox, EvidenceOutboxWorker
from .platform_client import PlatformSyncClient
from .platform_config import PlatformConfig
from .platform_context_store import PlatformContextStore
from .platform_deadline_worker import PlatformDeadlineWorker
from .review_store import ReviewStore
from .session_janitor import SessionJanitor, stale_session_timeout
from .session_log_service import SessionLogService
from .session_store import SessionStore
from .submission_coordinator import SubmissionCoordinator
from .training_record_automation import TrainingRecordRefresher


def _jupyter_labextension_paths():
    return [{
        "src": "labextension",
        "dest": "myextension"
    }]


def _jupyter_server_extension_points():
    return [{
        "module": "myextension"
    }]


def _load_jupyter_server_extension(server_app):
    """Registers the API handler to receive HTTP requests from the frontend extension.

    Parameters
    ----------
    server_app: jupyterlab.labapp.LabApp
        JupyterLab application instance
    """
    setup_route_handlers(server_app.web_app)
    name = "myextension"
    server_app.log.info(f"Registered {name} server extension")
    settings = server_app.web_app.settings
    lifecycle_key = "myextension_background_services_started"
    if not settings.get(lifecycle_key):
        root = resolve_log_root().expanduser().resolve()
        worker = settings.get("myextension_analysis_worker")
        worker_created = False
        janitor_created = False
        evidence_worker_created = False
        deadline_worker_created = False
        registered_shutdowns = []
        published = False
        service_keys = (
            "myextension_analysis_job_store",
            "myextension_analysis_worker",
            "myextension_session_janitor",
            "myextension_evidence_outbox",
            "myextension_evidence_worker",
            "myextension_submission_coordinator",
            "myextension_platform_deadline_worker",
            lifecycle_key,
        )
        prior_settings = {
            key: (key in settings, settings.get(key))
            for key in service_keys
        }
        session_store = (
            getattr(worker, "session_store", None)
            if worker is not None
            else None
        )
        if session_store is None:
            session_store = SessionStore(root)

        job_store = settings.get("myextension_analysis_job_store")
        if job_store is None and worker is not None:
            job_store = getattr(worker, "job_store", None)
        if job_store is None:
            job_store = AnalysisJobStore(root)
        review_store = ReviewStore(root)
        session_log_service = SessionLogService(
            root=root,
            session_store=session_store,
            job_store=job_store,
            review_store=review_store,
        )
        training_record_refresher = TrainingRecordRefresher(
            session_log_service,
            logger=server_app.log,
        )
        classroom_brief_refresher = ClassroomBriefRefresher(
            session_log_service,
            logger=server_app.log,
        )
        janitor = settings.get("myextension_session_janitor")
        evidence_outbox = settings.get("myextension_evidence_outbox")
        evidence_worker = settings.get("myextension_evidence_worker")
        submission_coordinator = settings.get("myextension_submission_coordinator")
        deadline_worker = settings.get("myextension_platform_deadline_worker")
        try:
            platform_config = PlatformConfig.from_env()
        except RuntimeError:
            platform_config = None
        try:
            if worker is None:
                worker = AnalysisWorker(
                    root,
                    job_store=job_store,
                    session_store=session_store,
                    terminal_callback=training_record_refresher.refresh,
                    autostart=False,
                )
                worker_created = True
            if janitor is None:
                janitor = SessionJanitor(
                    session_store,
                    timeout=stale_session_timeout(),
                    on_abandoned=classroom_brief_refresher.refresh,
                )
                janitor_created = True
            if (
                platform_config is not None
                and platform_config.student_mode
                and platform_config.sync_base_url is not None
            ):
                if evidence_outbox is None:
                    evidence_outbox = EvidenceOutbox(
                        root,
                        client=PlatformSyncClient(platform_config.sync_base_url),
                        context_store=PlatformContextStore(root),
                    )
                if evidence_worker is None:
                    evidence_worker = EvidenceOutboxWorker(evidence_outbox)
                    evidence_worker_created = True
                if submission_coordinator is None:
                    submission_coordinator = SubmissionCoordinator(
                        root,
                        session_store=session_store,
                        session_log_service=session_log_service,
                        outbox=evidence_outbox,
                        client=PlatformSyncClient(platform_config.sync_base_url),
                        context_store=PlatformContextStore(root),
                    )
                if deadline_worker is None:
                    deadline_worker = PlatformDeadlineWorker(
                        PlatformContextStore(root),
                        submission_coordinator,
                        interval_seconds=platform_config.deadline_poll_seconds,
                    )
                    deadline_worker_created = True

            recovered_job_ids = job_store.recover_interrupted()
            queued_job_ids = job_store.list_queued()
            for job_id in sorted(
                set(recovered_job_ids) | set(queued_job_ids)
            ):
                worker.enqueue(job_id)
            janitor.start()
            if evidence_worker is not None:
                evidence_worker.start()
            if deadline_worker is not None:
                deadline_worker.start()

            atexit.register(worker.shutdown)
            registered_shutdowns.append(worker.shutdown)
            atexit.register(janitor.shutdown)
            registered_shutdowns.append(janitor.shutdown)
            if evidence_worker is not None:
                atexit.register(evidence_worker.shutdown)
                registered_shutdowns.append(evidence_worker.shutdown)
            if deadline_worker is not None:
                atexit.register(deadline_worker.shutdown)
                registered_shutdowns.append(deadline_worker.shutdown)

            published = True
            settings["myextension_analysis_job_store"] = job_store
            settings["myextension_analysis_worker"] = worker
            settings["myextension_session_janitor"] = janitor
            if evidence_outbox is not None:
                settings["myextension_evidence_outbox"] = evidence_outbox
            if evidence_worker is not None:
                settings["myextension_evidence_worker"] = evidence_worker
            if submission_coordinator is not None:
                settings["myextension_submission_coordinator"] = submission_coordinator
            if deadline_worker is not None:
                settings["myextension_platform_deadline_worker"] = deadline_worker
            settings[lifecycle_key] = True
            # This is the final fallible startup operation. Once it succeeds,
            # the worker may execute and no lifecycle mutation remains.
            if worker_created:
                worker.start()
        except Exception:
            if published:
                for key, (existed, prior_value) in prior_settings.items():
                    if existed:
                        settings[key] = prior_value
                    else:
                        settings.pop(key, None)
            for shutdown in registered_shutdowns:
                try:
                    atexit.unregister(shutdown)
                except Exception:
                    pass
            if janitor_created and janitor is not None:
                try:
                    janitor.shutdown()
                except Exception:
                    pass
            if evidence_worker_created and evidence_worker is not None:
                try:
                    evidence_worker.shutdown()
                except Exception:
                    pass
            if deadline_worker_created and deadline_worker is not None:
                try:
                    deadline_worker.shutdown()
                except Exception:
                    pass
            if worker_created and worker is not None:
                try:
                    worker.shutdown()
                except Exception:
                    pass
            raise
