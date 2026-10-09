# Local diagnostics

These scripts are manual, read-only inspection helpers. They are not part of
the application launch path, packaging pipeline, or automated test suite.

Run them from any working directory with the project Python interpreter. Each
script locates the repository root by finding `pyproject.toml`, so it does not
depend on a user-specific absolute path.

Layout:

- `story/`: story input and production-form schema inspection helpers.

Do not place runtime logs, media outputs, database copies, or credentials in
this directory. Those belong below `项目资产/` or ignored `artifacts/`.
