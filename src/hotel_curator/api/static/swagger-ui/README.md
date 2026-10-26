# Vendored Swagger UI

Files for the API's interactive docs page (`/docs`), served by the API itself
so the page loads nothing from third-party hosts.

| | |
|---|---|
| Package | `swagger-ui-dist` 5.33.1 (npm, published 2026-10-01) |
| Licence | Apache-2.0 (`LICENSE`, `NOTICE`, `swagger-ui-bundle.js.LICENSE.txt`) |
| Tarball integrity | `sha512-H872wWkA53bFIsGgi7OWgmq+CRWw3nFQGdJWRqOB9wNwTm6e5ol34+qPDkV4AJlK+gglM2EsJiOOzsGsEGbluA==` (matches the registry) |
| `swagger-ui-bundle.js` SHA-256 | `050bc415ee7048dcd881682678f720264e7da5e373f7461d7c58c755305255f7` |
| `swagger-ui.css` SHA-256 | `1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321` |

`docs-init.js` is ours. To update: `npm pack swagger-ui-dist@<version>`,
check the tarball's integrity against `npm view swagger-ui-dist@<version>
dist.integrity`, copy the same files, and update the hashes here and in
`tests/api/test_docs.py`.
