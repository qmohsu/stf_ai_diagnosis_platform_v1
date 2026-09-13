"""Raw log file storage: one directory per vehicle (code design §5).

Layout under the storage root (``STF_V3_OBD_LOG_STORAGE_PATH``)::

    <root>/<vehicle_id>/<log_id>.<tsv|csv>

The directory name *is* the vehicle, so a copy or migration of the tree can
never lose ownership ("转存不丢归属").  ``obd_logs.raw_path`` stores the path
relative to the root, so relocating the volume needs no data fix-up.

Author: Xiangzhu Yan
"""

import os
import uuid
from pathlib import Path


class LogStorage:
    """Filesystem storage for uploaded log bytes.

    Attributes:
        root: Absolute storage root; created on first use.
    """

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def relative_path(self, vehicle_id: uuid.UUID, log_id: uuid.UUID, ext: str) -> str:
        """Returns the root-relative path for a log (what goes in the DB)."""
        return f"{vehicle_id}/{log_id}.{ext}"

    def absolute_path(self, raw_path: str) -> Path:
        """Resolves a stored ``raw_path`` under the root.

        Raises:
            ValueError: If ``raw_path`` escapes the root (defensive; the
                value comes from our own table, never from a client).
        """
        target = (self.root / raw_path).resolve()
        if self.root not in target.parents:
            raise ValueError(f"raw_path escapes storage root: {raw_path}")
        return target

    def write(self, raw_path: str, data: bytes) -> Path:
        """Writes ``data`` atomically (temp file + rename).

        A crash mid-write leaves at most a ``.part`` file, never a
        truncated final file that looks valid.

        Returns:
            The absolute path written.
        """
        target = self.absolute_path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        return target

    def delete(self, raw_path: str) -> None:
        """Removes a stored file if present (used to undo a failed insert)."""
        try:
            self.absolute_path(raw_path).unlink(missing_ok=True)
        except ValueError:
            pass

    def exists(self, raw_path: str) -> bool:
        """True when the stored file is on disk."""
        try:
            return self.absolute_path(raw_path).is_file()
        except ValueError:
            return False
