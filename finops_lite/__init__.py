"""
FinOps Lite - AWS FinOps CLI for cost visibility, optimization, and governance.

A professional-grade command-line tool that brings AWS cost management
directly to your terminal.
"""

__version__ = "0.4.0"
__author__ = "Diana"
__email__ = "diana@cloudandcapital.com"

__all__ = ["FinOpsConfig", "load_config", "__version__"]


def __getattr__(name):
    # Pure local billing/CCAC imports must not require an AWS SDK or credentials.
    if name in {"FinOpsConfig", "load_config"}:
        from .utils.config import FinOpsConfig, load_config

        return {"FinOpsConfig": FinOpsConfig, "load_config": load_config}[name]
    raise AttributeError(name)
