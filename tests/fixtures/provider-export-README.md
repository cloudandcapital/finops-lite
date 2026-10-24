# Microsoft export-format compatibility data

`microsoft-ea-focus-1000.csv.gz` contains the unchanged header and first 1,000
records of Microsoft's `EA-Cost-FOCUS_1.0.csv` at repository snapshot
`8b393911e0b11c25839216d3cdf16c4bec32eae8`:
https://github.com/microsoft/finops-toolkit/blob/8b393911e0b11c25839216d3cdf16c4bec32eae8/src/open-data/dataset-examples/EA-Cost-FOCUS_1.0.csv

Copyright Microsoft Corporation, MIT license; retained in
`LICENSE-microsoft-finops-toolkit.txt`. Microsoft describes this as EA demo data
for understanding the export structure. This is format testing, not business
reporting or a current customer-export certification. The current v14 release
was published April 29, 2026; these records date from June 12, 2024.

The full published LFS object is 204,354,862 bytes, advertised SHA-256
`9b192e205da384caecb627e8a4281a0229195d96a8be24bce66057b25c6a8603`.
Only the contiguous leading records were downloaded, without rewriting columns,
amounts, timestamps, BOM or line endings. The full object was not downloaded or
independently hashed. The local deterministic Gzip wrapper (mtime zero) is not
an authentic provider-delivered compressed file.

Expanded prefix: 1,498,396 bytes, SHA-256
`5766878f44ac4a143a6c45c401ab2bd736876ca2ff70a290a82d25dd2344d6ed`.
Gzip: 130,534 bytes, SHA-256
`d13586cc7078ef5ecb23ee5be0ba74874f78325027caff618448cad7e92d88e2`.

Independent exact USD totals: positive `111.458936152662569`, negative
`-28.112752516262428`, net `83.346183636400141`. All 96 original columns remain.
There are no identical full rows. UTC boundaries use minute precision, for
example `2024-06-12T00:00Z`. This partial file does not establish full invoice
coverage, FOCUS 1.2 conformance, current MCA layouts or current account access.

`google-cost-table-document.csv` is invented contract data, not a captured
customer file. It adds recognized document headers to the existing Google
fixture, preserving BOM/CRLF. The header invoice date intentionally differs
from the September reporting month and its invoice total is deliberately
999.00. Neither becomes a charge or overrides the explicitly confirmed period.
The Cost table's own Total control remains 108.83 against exact net 108.830007.
