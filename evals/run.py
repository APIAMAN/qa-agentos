"""Entry point: ``uv run --project evals python evals/run.py --mode reference|replay|live``."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from qa_evals.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
