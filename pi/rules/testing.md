# Testing and Linting
- use `prek` for linting and static analysis
- avoid unit tests which test the implementation rather than the interface
- avoid tautological tests
- prefer property testing approaches and tools like `hypothesis`
- running the full test harness must be fast -- consolidate tests, reduce test scope for capturing precise issues, avoid low-value tests
- transient and flaky tests must be identified for later follow-up
- do not disable tests or linters without confirmation, fix the issue instead
