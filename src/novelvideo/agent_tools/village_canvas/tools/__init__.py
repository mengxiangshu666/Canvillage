"""Declared capabilities for the Village canvas agent (T-158).

Each domain module exports `ToolSpec`s. The package assembler rebuilds direct
tool schemas, capability cards, and handler lookup from those declarations in
the order frozen by `tools/order.py`.
"""

from __future__ import annotations

from .api import API_SPECS
from .canvas import CANVAS_SPECS
from .canvas_writes import CANVAS_WRITE_SPECS
from .camera import CAMERA_SPECS
from .creative_media import CREATIVE_MEDIA_SPECS
from .creative_planning import CREATIVE_PLANNING_SPECS
from .creative_video import CREATIVE_VIDEO_SPECS
from .director import DIRECTOR_SPECS
from .frontend_ui import FRONTEND_UI_SPECS
from .judgment import JUDGMENT_SPECS
from .production import PRODUCTION_SPECS
from .project import PROJECT_SPECS
from .research import RESEARCH_SPECS
from .spec import ToolSpec
from .state import STATE_SPECS
from .story_lab import STORY_LAB_SPECS
from .tasks import TASK_SPECS
from .ui import UI_SPECS
from .video_operations import VIDEO_OPERATION_SPECS
from .vision import VISION_SPECS
from .workflow import WORKFLOW_SPECS
from .workflow_runs import WORKFLOW_RUN_SPECS


SPECS: tuple[ToolSpec, ...] = (
    *API_SPECS,
    *CANVAS_SPECS,
    *CANVAS_WRITE_SPECS,
    *FRONTEND_UI_SPECS,
    *VIDEO_OPERATION_SPECS,
    *CAMERA_SPECS,
    *DIRECTOR_SPECS,
    *JUDGMENT_SPECS,
    *RESEARCH_SPECS,
    *VISION_SPECS,
    *STORY_LAB_SPECS,
    *PROJECT_SPECS,
    *PRODUCTION_SPECS,
    *TASK_SPECS,
    *WORKFLOW_SPECS,
    *WORKFLOW_RUN_SPECS,
    *CREATIVE_PLANNING_SPECS,
    *CREATIVE_MEDIA_SPECS,
    *CREATIVE_VIDEO_SPECS,
    *UI_SPECS,
    *STATE_SPECS,
)

__all__ = ["SPECS", "ToolSpec"]
