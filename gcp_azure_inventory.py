#!/usr/bin/env python3
"""Thin entry point for the gcp_inventory package.

The implementation now lives in the ``gcp_inventory`` package. This wrapper is
kept so the original command keeps working:

    python gcp_azure_inventory.py --scope organizations/123456789

You can equivalently run the package module directly:

    python -m gcp_inventory --scope organizations/123456789
"""
from gcp_inventory.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
