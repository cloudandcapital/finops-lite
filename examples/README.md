# Sample billing files

These small illustrative files show different billing shapes used in development. They are not customer billing records or certified FOCUS datasets.

| File | Shape and purpose |
| --- | --- |
| [aws-billing-sample.csv](aws-billing-sample.csv) | Service-level AWS-shaped working export with cost, charge-period, usage and allocation fields |
| [azure-billing-sample.csv](azure-billing-sample.csv) | Azure-shaped sample with billing-currency cost, subscription and resource fields |
| [gcp-billing-sample.csv](gcp-billing-sample.csv) | Google-shaped sample with service, project, cost and usage fields |

## Choose the supported path

- Start with the [credential-free CCAC demo](../README.md#five-minute-credential-free-demo) for deterministic canonical output.
- Use the [billing CSV input guide](../docs/billing-csv.md) to check the supported supplied-file profiles and their coverage rules.
- Use the [project README](../README.md#current-scope) to check current native provider support and the limits of experimental normalization helpers.
- See the [SQL walkthrough](../sql-analysis/README.md) for a separate sample dataset and analysis queries.

The Azure and Google samples support development of file-handling helpers; their presence does not establish native billing API integrations. The AWS working export and legacy compatibility names do not establish official FOCUS conformance. See the project's [FOCUS status](../README.md#focus-status) before making or relying on a conformance claim.
