"""Local state database reads (T-156).

Two broker capabilities that let the agent read the real tables instead of
guessing column names, and run one bounded read-only query itself. The card text
is the text the model reads; it is copied verbatim from the source part this
declaration replaced and frozen by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


STATE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="state.sql.schema",
        handler_name="_handle_state_sql_schema",
        card={
            "id": "state.sql.schema",
            "domain": "state",
            "purpose": "读取本地状态库的真实表名、视图名与列名；写查询前先用它核对列名，不要凭记忆猜字段。库用逻辑名：project_data / workflow_runs / chat / durable_tasks。传 table 只看一张表（列数上限更宽）；读数里 truncated / columns_truncated / unreadable_objects 说明列表是否被截、哪个对象读不出来。",
            "required_args": ["database"],
            "optional_args": ["table", "project_id"],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "数据库 表结构 字段 列名 视图 schema tables columns state sql",
        },
    ),
    ToolSpec(
        id="state.sql.query",
        handler_name="_handle_state_sql_query",
        card={
            "id": "state.sql.query",
            "domain": "state",
            "purpose": "对本地状态库执行单条只读 SELECT/WITH 查询，跨表统计脚本行、镜头、任务、工作流与对话事实；只读、有条数上限、不接受写语句，也读不到模型密钥。",
            "required_args": ["database", "sql"],
            "optional_args": ["project_id", "max_rows"],
            "cost": "free",
            "side_effect": "read",
            "search_terms": "查询 统计 只读 跨表 镜头 工作流 任务 对话 sql select query state",
        },
    ),
)
