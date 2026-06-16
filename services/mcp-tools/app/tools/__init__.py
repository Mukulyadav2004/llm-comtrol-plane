"""Tool registry. Importing the toolset modules registers their tools."""
from app.tools.base import REGISTRY, Tool, tool  # noqa: F401

# Import for side effect: each module registers its tools on import.
from app.tools import utility  # noqa: F401,E402
from app.tools import knowledge  # noqa: F401,E402
