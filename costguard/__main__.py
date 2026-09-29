"""Allow running CostGuard as ``python -m costguard``."""

import sys

from costguard.cli import main

sys.exit(main())
