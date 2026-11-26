Node.js / TypeScript conventions: Node 20+, ES modules (`"type": "module"`), source in `src/`, tests in `tests/`
named `*.test.js|ts`. Use the repo's test runner; for new projects with no dependencies use the built-in `node:test`
and `node:assert/strict` with `"test": "node --test"`. Handle money as integer minor units (paise/cents), never
floats. Validate inputs at module boundaries and throw `RangeError`/`TypeError` with clear messages. Keep
`package.json` scripts `test` (and `build`, `lint`, `typecheck` when used) working, because CI and the orchestrator run them.
