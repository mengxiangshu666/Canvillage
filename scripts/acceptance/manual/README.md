# Manual acceptance checks

Scripts in this directory are intentionally excluded from automated CI. They
may require a live local service, a specific existing project/canvas, or a
user-approved paid-media budget.

`paid_agent_video_ab.py` is a historical manual A/B runner for a real video
task. It is retained for reproducibility, but it must only be started after
the active project, canvas, node, model route, and budget are deliberately
reviewed. Its output is written under `项目资产/state/agent_paid_eval/`.
