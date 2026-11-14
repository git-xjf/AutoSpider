"""Canonical entry point; the old module path remains as a compatibility shim."""

from services.easyspider_mcp.server import main, mcp

__all__ = ["main", "mcp"]


if __name__ == "__main__":
    main()
