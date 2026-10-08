"""Worker : un seul processus, un seul traitement GPU à la fois (PLAN.md §5).

File d'attente dans la base. Prise d'un job atomique (UPDATE conditionnel : sûr même si deux workers tournaient).
Reprise : un job « running » dont le verrou est trop ancien (worker tué) est remis en file. Le moteur OCR (donc
llama-server et la VRAM) est démarré à la demande et arrêté après une inactivité courte, car le GPU est partagé avec
d'autres applications. Ordre de service : exports d'abord (quelques secondes), puis tour à tour entre utilisateurs,
pour qu'un lot de cent fichiers ne bloque pas le document isolé d'un collègue.

État publié dans <data_dir>/run/worker.json (battement de cœur) : l'interface signale un worker arrêté au lieu de
laisser les documents « en attente » sans explication.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, init_engine
from app.models import DocStatus, Document, Job, JobStatus, utcnow
from app.services import app_settings, pipeline
from app.services.gpu import LlamaServerError, free_vram_mb
from app.services.ocr import OcrConfigError, OcrEngine, OcrUnavailable
from app.services.ocr_config import OcrConfig

log = logging.getLogger("ocr.worker")
GPU_RETRY_SECONDS = 20
HEARTBEAT_SECONDS = 5
MAX_ATTEMPTS = 3  # un document qui fait tomber le worker trois fois de suite est mis en échec


def make_engine(cfg: OcrConfig | None = None) -> OcrEngine:
    cfg = cfg or OcrConfig.from_env()
    if cfg.backend == "dummy":
        from app.services.ocr.dummy import DummyEngine
        return DummyEngine()
    from app.services.ocr.surya2 import Surya2Engine
    return Surya2Engine(config=cfg)


def status_file() -> Path:
    return get_settings().data_dir / "run" / "worker.json"


def read_status() -> dict | None:
    """État publié par le worker (None : jamais démarré). `alive` : battement de cœur récent."""
    try:
        data = json.loads(status_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["alive"] = time.time() - float(data.get("at", 0)) < 6 * HEARTBEAT_SECONDS and data.get("state") != "stopped"
    return data


class SingleInstance:
    """Verrou de fichier : un second worker lancé par erreur s'arrête au lieu de se disputer le GPU et le port."""

    def __init__(self, path: Path):
        self.path, self._fp = path, None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fp = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fp.close()
            return False
        self._fp = fp
        return True

    def release(self) -> None:
        if self._fp is not None:
            try:
                self._fp.close()
            finally:
                self._fp = None


class Worker:
    def __init__(self, engine_factory=make_engine):
        self.engine_factory = engine_factory
        self.engine: OcrEngine | None = None
        self.engine_key: tuple | None = None
        self.engine_last_used = 0.0
        self._stop = False
        self._last_purge = 0.0
        self._last_beat = 0.0
        self._served: dict[int, float] = {}  # user_id -> dernier service (tour à tour)
        self.state, self.current_doc = "idle", None

    # ---------------------------------------------------------------- moteur OCR partagé
    def acquire_engine(self, db: Session, cfg: OcrConfig | None = None) -> OcrEngine:
        cfg = cfg or OcrConfig.load(db)
        if self.engine is not None and self.engine_key != cfg.engine_key():
            log.info("réglages du moteur OCR modifiés : redémarrage du moteur")
            self.release_engine(force=True)
        if self.engine is None:
            needed = app_settings.get_setting(db, "gpu_min_free_mb")
            free = free_vram_mb() if cfg.backend != "dummy" else None
            if free is not None and free < needed:
                raise OcrUnavailable(f"VRAM libre insuffisante ({free} Mo < {needed} Mo)")
            eng = self.engine_factory(cfg) if self.engine_factory is make_engine else self.engine_factory()
            try:
                eng.start()
            except LlamaServerError as e:
                if e.permanent:
                    raise OcrConfigError(str(e)) from e
                raise OcrUnavailable(str(e)) from e
            self.engine, self.engine_key = eng, cfg.engine_key()
        if hasattr(self.engine, "config"):
            self.engine.config = cfg  # réglages lus à chaque page (effacement des tampons...)
        self.engine_last_used = time.monotonic()
        return self.engine

    def release_engine(self, force: bool = False, idle_minutes: int = 0) -> None:
        if self.engine is None:
            return
        if force or time.monotonic() - self.engine_last_used >= idle_minutes * 60:
            try:
                self.engine.stop()
            except Exception as e:  # noqa: BLE001 - l'arrêt ne doit jamais faire tomber le worker
                log.warning("arrêt du moteur OCR en erreur (%s)", type(e).__name__)
            finally:
                self.engine, self.engine_key = None, None
                log.info("moteur OCR arrêté (VRAM libérée)")

    def engine_mode(self) -> str:
        if self.engine is None:
            return "off"
        server = getattr(self.engine, "server", None)
        return getattr(server, "mode", "gpu") if server is not None else "on"

    # ---------------------------------------------------------------- file d'attente
    def requeue_stale(self, db: Session, all_running: bool = False) -> int:
        """Jobs « running » abandonnés (worker tué). `all_running` : au démarrage, sous le verrou d'instance unique,
        aucun autre worker ne peut les tenir : ils sont repris tout de suite au lieu d'attendre job_stale_minutes."""
        limit = utcnow() - timedelta(minutes=get_settings().job_stale_minutes)
        q = select(Job).where(Job.status == JobStatus.running.value)
        stale = db.scalars(q if all_running else q.where(Job.locked_at < limit)).all()
        for j in stale:
            if j.attempts >= MAX_ATTEMPTS:
                j.status, j.error = JobStatus.failed.value, "TooManyAttempts"
                doc = db.get(Document, j.document_id)
                if doc is not None:
                    doc.status = DocStatus.failed.value
                    doc.error = "Le traitement a été interrompu plusieurs fois (arrêt du service ou du poste ?)."
            else:
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

    def _pick(self, db: Session) -> int | None:
        """Exports d'abord, puis tour à tour entre utilisateurs (le moins récemment servi), du plus ancien au récent."""
        now = utcnow()
        rows = db.execute(
            select(Job.id, Job.type, Document.user_id).join(Document, Document.id == Job.document_id)
            .where(Job.status == JobStatus.queued.value, (Job.locked_at.is_(None)) | (Job.locked_at < now))
            .order_by(Job.id).limit(2000)).all()
        if not rows:
            return None
        exports = [r for r in rows if r.type == "export"]
        if exports:
            return exports[0].id
        first: dict[int, int] = {}
        for r in rows:
            first.setdefault(r.user_id, r.id)
        user = min(first, key=lambda u: (self._served.get(u, 0.0), first[u]))
        return first[user]

    def claim(self, db: Session) -> Job | None:
        for _ in range(5):
            job_id = self._pick(db)
            if job_id is None:
                return None
            now = utcnow()
            res = db.execute(update(Job).where(Job.id == job_id, Job.status == JobStatus.queued.value)
                             .values(status=JobStatus.running.value, locked_at=now, attempts=Job.attempts + 1))
            db.commit()
            if res.rowcount == 1:
                job = db.get(Job, job_id)
                db.refresh(job)
                doc = db.get(Document, job.document_id)
                if doc is not None:
                    self._served[doc.user_id] = time.monotonic()
                return job
        return None

    # ---------------------------------------------------------------- un job
    def handle(self, db: Session, job: Job) -> None:
        doc = db.get(Document, job.document_id)
        if doc is None or doc.status == DocStatus.cancelled.value:  # supprimé ou annulé entre-temps
            job.status = JobStatus.done.value
            db.commit()
            return
        self.state, self.current_doc = "working", doc.id
        self.heartbeat(force=True)
        try:
            if job.type == "export":
                pipeline.export_docx(db, doc)
            else:
                self._process(db, doc)
            job.status = JobStatus.done.value
            db.commit()
        except pipeline.Cancelled:
            db.rollback()
            doc = db.get(Document, job.document_id)
            job = db.get(Job, job.id)
            if job is not None:
                job.status = JobStatus.done.value
            if doc is not None:
                doc.status, doc.error, doc.cancel_requested = DocStatus.cancelled.value, None, False
                pipeline.cleanup_temporary(doc)
            db.commit()
            log.info("document %s : annulé", job.document_id if job else "?")
        except OcrUnavailable:
            db.rollback()
            doc = db.get(Document, job.document_id)
            job = db.get(Job, job.id)
            if doc is None or job is None:
                return
            doc.status = DocStatus.waiting_gpu.value
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
                doc.error = ("Moteur OCR mal configuré : contactez l'administrateur (Administration > Moteur OCR)."
                             if isinstance(e, OcrConfigError) else f"Échec du traitement ({type(e).__name__})")
            db.commit()
            log.error("document %s : échec (%s)", job.document_id, type(e).__name__)
        finally:
            self.state, self.current_doc = "idle", None

    def _process(self, db: Session, doc: Document) -> None:
        cfg = OcrConfig.load(db)
        opts = pipeline.doc_options(doc)
        if doc.started_at is None:
            doc.started_at = utcnow()
            db.commit()
        pipeline.render_and_preprocess(db, doc, cfg)
        db.refresh(doc)
        if all(p.status == "error" for p in doc.pages):
            raise RuntimeError("aucune page lisible")
        if any(p.status == "rendered" for p in doc.pages):
            engine = self.acquire_engine(db, cfg)
            doc.status = DocStatus.ocr.value
            db.commit()
            pipeline.run_ocr(db, doc, engine)
            self.engine_last_used = time.monotonic()
            db.refresh(doc)
        if not any(p.status == "ocr_done" for p in doc.pages):
            raise RuntimeError("aucune page reconnue")
        if cfg.second_reader and pipeline.second_reading_candidates(doc, cfg):
            free = free_vram_mb()
            if self.engine is not None and free is not None and free < 2600:
                self.release_engine(force=True)  # 4 Go : un seul serveur OCR à la fois sur le GPU partagé
            doc.options = {**(doc.options or {}), "second_stats": pipeline.run_second_reading(db, doc, cfg)}
            db.commit()
        stats = pipeline.run_correction(db, doc)
        doc.options = {**(doc.options or {}), "llm_stats": stats}
        db.commit()
        pipeline.check_cancel(db, doc)
        if opts["review"]:
            pipeline.cleanup_temporary(doc)
            doc.status, doc.progress = DocStatus.review.value, 0.95
            db.commit()
        else:
            pipeline.export_docx(db, doc)
            pipeline.cleanup_temporary(doc)
            db.commit()

    # ---------------------------------------------------------------- état publié
    def heartbeat(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_beat < HEARTBEAT_SECONDS:
            return
        self._last_beat = now
        data = {"at": now, "pid": os.getpid(), "state": self.state, "document": self.current_doc,
                "engine": self.engine_mode()}
        f = status_file()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass

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

    def request_stop(self, *_args) -> None:
        self._stop = True

    def run_forever(self) -> None:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK : Ctrl+Pause, envoyé par le superviseur (Windows)
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, self.request_stop)
                except (ValueError, OSError):
                    pass
        log.info("worker démarré (pid %s)", os.getpid())
        try:
            with SessionLocal() as db:
                n = self.requeue_stale(db, all_running=True)
            if n:
                log.info("%s traitement(s) interrompu(s) repris", n)
        except Exception:  # noqa: BLE001
            log.exception("reprise des traitements interrompus impossible")
        poll = get_settings().worker_poll_seconds
        errors = 0
        while not self._stop:
            self.heartbeat()
            try:
                busy = self.run_once()
                errors = 0
                if not busy:
                    self._sleep(poll)
            except Exception:  # noqa: BLE001 - base momentanément verrouillée, disque plein... : on réessaie
                errors += 1
                log.exception("erreur dans la boucle du worker")
                self._sleep(min(60.0, poll * 2 ** min(errors, 5)))
        self.release_engine(force=True)
        self.state = "stopped"
        self.heartbeat(force=True)
        log.info("worker arrêté")

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
            self.heartbeat()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    lock = SingleInstance(get_settings().data_dir / "run" / "worker.lock")
    if not lock.acquire():
        log.error("un autre worker tourne déjà sur ce dossier de données : arrêt")
        return 3
    try:
        init_engine()
        Worker().run_forever()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
