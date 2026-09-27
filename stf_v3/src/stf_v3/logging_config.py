"""Logging: JSON lines to stdout and, for long-running processes, to a file.

Business logs (structlog) and framework logs (stdlib: uvicorn, procrastinate,
SQLAlchemy) share ONE exit (PROD-15A FM-63): structlog hands every event to
stdlib, and the root handlers render the same JSON line to stdout (container
log / journal) and, when ``STF_V3_LOG_DIR`` is set, to ``<dir>/<process>.log``.

The file survives container rebuilds (it lives on a volume / host directory),
rolls at UTC midnight into ``<process>.log.<YYYY-MM-DD>.gz``, keeps
``STF_V3_LOG_KEEP_DAYS`` days (30) and at most ``STF_V3_LOG_MAX_MB`` (2048)
per process — the oldest rolled day goes first.  Every file is 0600, the
directory 0700 (FM-49).  Each process writes only its own file (FM-25).

These variables are read here, not in ``settings.py`` (a golden-gate managed
path).

Author: Xiangzhu Yan
"""

import datetime as dt
import gzip
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

import structlog

_SHARED: List[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]
_ROLLED = re.compile(r"\.(\d{4}-\d{2}-\d{2})(?:-\d+)?\.gz$")
_MARK = "_stf_v3_handler"


class DailyFileHandler(logging.FileHandler):
    """One process's log file, rolled daily (UTC), gzipped, pruned by age and size.

    Args:
        path: The live file, e.g. ``/app/data/logs/api.log``.
        keep_days: Rolled days kept.
        max_bytes: Cap on the live file plus this process's rolled files.
        clock: Seconds since the epoch; injectable for tests.
    """

    def __init__(self, path: Path, keep_days: int = 30, max_bytes: int = 2048 * 2**20,
                 clock: Callable[[], float] = time.time) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.keep_days, self.max_bytes, self._clock = keep_days, max_bytes, clock
        super().__init__(path, encoding="utf-8", delay=True)
        # A file left by a run on an earlier day is rolled at the first write.
        self._day = (self._day_of(path.stat().st_mtime) if path.exists()
                     else self._day_of(clock()))
        self.prune()

    @staticmethod
    def _day_of(ts: float) -> str:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")

    def _open(self):  # type: ignore[no-untyped-def]
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.baseFilename, 0o600)          # a file made with a looser mode
        return os.fdopen(fd, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        """Rolls over first when the UTC day changed, then writes the line."""
        today = self._day_of(self._clock())
        if today != self._day:
            try:
                self._rollover(self._day)
            except OSError:
                self.handleError(record)
            self._day = today
        super().emit(record)

    def _rollover(self, day: str) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        live = Path(self.baseFilename)
        if live.exists() and live.stat().st_size:
            dest = live.with_name(f"{live.name}.{day}.gz")
            n = 0
            while dest.exists():                     # never overwrite a rolled day
                n += 1
                dest = live.with_name(f"{live.name}.{day}-{n}.gz")
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with open(live, "rb") as src, os.fdopen(fd, "wb") as raw, \
                    gzip.GzipFile(fileobj=raw, mode="wb") as gz:
                shutil.copyfileobj(src, gz)
            live.unlink()
        self.prune()

    def rolled_files(self) -> List[Path]:
        """This process's rolled files, oldest first."""
        live = Path(self.baseFilename)
        found = [p for p in live.parent.glob(live.name + ".*.gz") if _ROLLED.search(p.name)]
        return sorted(found, key=lambda p: (_ROLLED.search(p.name).group(1), p.name))  # type: ignore[union-attr]

    def prune(self) -> None:
        """Deletes rolled days older than ``keep_days``, then the oldest over the size cap."""
        cutoff = self._day_of(self._clock() - self.keep_days * 86400)
        rolled = self.rolled_files()
        for p in [p for p in rolled if _ROLLED.search(p.name).group(1) < cutoff]:  # type: ignore[union-attr]
            p.unlink(missing_ok=True)
            rolled.remove(p)
        live = Path(self.baseFilename)
        total = (live.stat().st_size if live.exists() else 0) + sum(p.stat().st_size for p in rolled)
        while rolled and total > self.max_bytes:
            oldest = rolled.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)


def file_handler_from_env(process: str) -> Optional[DailyFileHandler]:
    """The file handler for ``process`` when ``STF_V3_LOG_DIR`` is set, else None."""
    log_dir = os.environ.get("STF_V3_LOG_DIR", "").strip()
    if not log_dir:
        return None
    return DailyFileHandler(
        Path(log_dir) / f"{process}.log",
        keep_days=int(os.environ.get("STF_V3_LOG_KEEP_DAYS", "30")),
        max_bytes=int(os.environ.get("STF_V3_LOG_MAX_MB", "2048")) * 2**20,
    )


def configure_logging(level: str = "INFO", process: Optional[str] = None) -> None:
    """Routes structlog and stdlib logging to stdout (and a file) as JSON.

    Args:
        level: Log level name (``DEBUG``, ``INFO`` ...).
        process: ``api`` / ``worker`` / ``gpu-worker`` for a long-running
            process that should also write ``<STF_V3_LOG_DIR>/<process>.log``;
            None (scripts, tests) = stdout only.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*_SHARED, structlog.stdlib.add_logger_name],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,      # framework tracebacks
            structlog.processors.JSONRenderer(),
        ],
    )
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_handler = file_handler_from_env(process) if process else None
    if file_handler is not None:
        handlers.append(file_handler)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(old)
        old.close()
    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _MARK, True)
        root.addHandler(h)
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; send its lines through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    structlog.configure(
        processors=[
            *_SHARED,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
