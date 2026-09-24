"""``python -m stf_v3.evals`` entry point."""

import os
import sys

from stf_v3.evals.cli import main
from stf_v3.logging_config import configure_logging

if __name__ == "__main__":
    # The agent logs every event at INFO; an eval only needs warnings (the
    # per-golden progress lines are printed separately).
    configure_logging(os.environ.get("STF_V3_EVAL_LOG_LEVEL", "WARNING"))
    sys.exit(main())
