"""Deprecated NiceGUI UI package.

The supported UI is the React frontend backed by the REST API
(`novelvideo.api.app:app`). The old NiceGUI implementation has been removed to
prevent new product code from depending on it.
"""


def run_nicegui_app(*_args, **_kwargs):
    raise RuntimeError(
        "NiceGUI has been deprecated. Start the REST API with "
        "`uvicorn novelvideo.api.app:app --host 0.0.0.0 --port 8780` "
        "and use the bundled React frontend instead."
    )


__all__ = ["run_nicegui_app"]
