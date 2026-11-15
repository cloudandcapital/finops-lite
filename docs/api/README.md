# Implementation reference

Start with the [project README](../../README.md) for supported CLI commands, installation, data scope and verification requirements.

This index links to the Python implementation behind the CLI:

| Module | Area |
| --- | --- |
| [cli.py](../../finops_lite/cli.py) | CLI commands and options |
| [ccac.py](../../finops_lite/ccac.py) | CCAC result construction |
| [billing.py](../../finops_lite/billing.py) | Supplied billing file handling |
| [core/cost_explorer.py](../../finops_lite/core/cost_explorer.py) | AWS Cost Explorer integration |
| [signals/from_services.py](../../finops_lite/signals/from_services.py) | Service-level decision signals |
| [summary.py](../../finops_lite/summary.py) | Summary construction |

For worked inputs, see the [examples and walkthroughs](../examples/README.md). Source links describe the implementation at the selected Git revision; check the project README before depending on an interface.
