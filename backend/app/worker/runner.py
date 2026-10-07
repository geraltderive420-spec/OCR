"""Worker : un seul processus, un seul traitement GPU à la fois (PLAN.md §5).

File d'attente dans la base (`SELECT … FOR UPDATE SKIP LOCKED`). Reprise : un job « running » dont le verrou est
trop ancien (worker tué) est remis en file. Le moteur OCR (donc llama-server et la VRAM) est démarré à la demande
et arrêté après une inactivité courte, car le GPU est partagé avec d'autres applications.
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, init_engine
from app.models import DocStatus, Document, Job, JobStatus, utcnow
from app.services import app_settings, pipeline, storage
from app.services.gpu import LlamaServerError, free_vram_mb
from app.services.ocr import OcrConfigError, OcrEngine, OcrUnavailable

log = logging.getLogger("ocr.worker")
GPU_RETRY_SECONDS = 20


def make_engine() -> OcrEngine:
    backend = get_settings().ocr_backend
    if backend == "dummy":
        from app.services.ocr.dummy import DummyEngine
        return DummyEngine()
    from app.services.ocr.surya2 import Surya2Engine
    return Surya2Engine()


class Worker:
    def __init__(self, engine_factory=make_engine):
        self.engine_factory = engine_factory
        self.engine: OcrEngine | None = None
        self.engine_last_used = 0.0
        self._stop = False
        self._last_purge = 0.0

    # ---------------------------------------------------------------- moteur OCR partagé
    def acquire_engine(self, db: Session) -> OcrEngine:
        if self.engine is None:
            needed = app_settings.get_setting(db, "gpu_min_free_mb")
            free = free_vram_mb() if get_settings().ocr_backend != "dummy" else None
            if free is not None and free < needed:
                raise OcrUnavailable(f"VRAM libre insuffisante ({free} Mo < {needed} Mo)")
            eng = self.engine_factory()
            try:
                eng.start()
            except LlamaServerError as e:
                if e.permanent:
                    raise OcrConfigError(str(e)) from e
                raise OcrUnavailable(str(e)) from e
            self.engine = eng
        self.engine_last_used = time.monotonic()
        return self.engine

    def release_engine(self, force: bool = False, idle_minutes: int = 0) -> None:
        if self.engine is None:
            return
        if force or time.monotonic() - self.engine_last_used >= idle_minutes * 60:
            try:
                self.engine.stop()
            finally:
                self.engine = None
                log.info("moteur OCR arrêté (VRAM libérée)")

    # ---------------------------------------------------------------- file d'attente
    def requeue_stale(self, db: Session) -> int:
        limit = utcnow() - timedelta(minutes=get_settings().job_stale_minutes)
        stale = db.scalars(select(Job).where(Job.status == JobStatus.running.value, Job.locked_at < limit)).all()
        for j in stale:
            j.status, j.locked_at = JobStatus.queued.value, None
        db.commit()
        return len(stale)

    def maybe_purge(self, db: Session) -> None:
        """Purge de rétention au plus une fois par heure."""
        if time.monotonic() - self._last_purge < 3600 and self._last_purge:
            return
        self._last_purge = time.monotonic()
        from app.services.retention import purge_expired
        purge_expired(db)

    def claim(self, db: Session) -> Job | None:
        now = utcnow()
        q = (select(Job).where(Job.status == JobStatus.queued.value, (Job.locked_at.is_(None)) | (Job.locked_at < now))
             .order_by(Job.id).limit(1).with_for_update(skip_locked=True))
        job = db.scalars(q).first()
        if job is None:
            return None
        job.status, job.locked_at, job.attempts = JobStatus.running.value, now, job.attempts + 1
        db.commit()
        return job

    # ---------------------------------------------------------------- un job
    def handle(self, db: Session, job: Job) -> None:
        doc = db.get(Document, job.document_id)
        if doc is None:  # document supprimé entre-temps
            job.status = JobStatus.done.value
            db.commit()
            return
        try:
            if job.type == "export":
                pipeline.export_docx(db, doc)
            else:
                self._process(db, doc)
            job.status = JobStatus.done.value
            db.commit()
        except OcrUnavailable:
            db.rollback()
            doc = db.get(Document, job.document_id)
            doc.status = DocStatus.waiting_gpu.value
            job = db.get(Job, job.id)
            job.status, job.locked_at = JobStatus.queued.value, utcnow() + timedelta(seconds=GPU_RETRY_SECONDS)
            job.attempts = max(0, job.attempts - 1)  # l'attente du GPU n'est pas un échec
            db.commit()
            log.info("document %s : en attente du GPU", doc.id)
        except Exception as e:  # noqa: BLE001 - aucun message d'exception : il pourrait contenir du contenu
            db.rollback()
            doc = db.get(Document, job.document_id)
            job = db.get(Job, job.id)
            if job is None:
                return
            job.status, job.error = JobStatus.failed.value, type(e).__name__
            if doc is not None:  # le document a pu être supprimé pendant le traitement
                doc.status = DocStatus.failed.value
                doc.error = ("Moteur OCR mal configuré : contactez l'administrateur (voir python -m app.cli check)."
                             if isinstance(e, OcrConfigError) else f"Échec du traitement ({type(e).__name__})")
            db.commit()
            log.error("document %s : échec (%s)", job.document_id, type(e).__name__)

    def _process(self, db: Session, doc: Document) -> None:
        opts = pipeline.doc_options(doc)
        pipeline.render_and_preprocess(db, doc)
        db.refresh(doc)
        if all(p.status == "error" for p in doc.pages):
            raise RuntimeError("aucune page lisible")
        if any(p.status == "rendered" for p in doc.pages):
            engine = self.acquire_engine(db)
            doc.status = DocStatus.ocr.value
            db.commit()
            pipeline.run_ocr(db, doc, engine)
            db.refresh(doc)
        if not any(p.status == "ocr_done" for p in doc.pages):
            raise RuntimeError("aucune page reconnue")
        stats = pipeline.run_correction(db, doc)
        doc.options = {**(doc.options or {}), "llm_stats": stats}
        db.commit()
        if opts["review"]:
            pipeline.cleanup_temporary(doc)
            doc.status, doc.progress = DocStatus.review.value, 0.95
            db.commit()
        else:
            pipeline.export_docx(db, doc)
            pipeline.cleanup_temporary(doc)
            db.commit()

    # ---------------------------------------------------------------- boucle
    def run_once(self) -> bool:
        """Traite au plus un job. Retourne True s'il y en avait un."""
        db = SessionLocal()
        try:
            self.requeue_stale(db)
            self.maybe_purge(db)
            job = self.claim(db)
            if job is None:
                self.release_engine(idle_minutes=app_settings.get_setting(db, "gpu_idle_unload_minutes"))
                return False
            self.handle(db, job)
            return True
        finally:
            db.close()

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_stop", True))
        try:
            signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))
        except (ValueError, AttributeError):
            pass
        log.info("worker démarré")
        poll = get_settings().worker_poll_seconds
        while not self._stop:
            try:
                if not self.run_once():
                    time.sleep(poll)
            except Exception:  # noqa: BLE001
                log.exception("erreur dans la boucle du worker")
                time.sleep(poll * 2)
        self.release_engine(force=True)
        log.info("worker arrêté")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_engine()
    Worker().run_forever()


if __name__ == "__main__":
    main()
