"""The canvas Agent's read-only SQL surface must stay read-only and bounded."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from novelvideo import state_sql


@pytest.fixture(autouse=True)
def _unwrap_tool_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match the plugin test suite: assert on payloads, not JSON wrappers."""

    from novelvideo.agent_tools import village_canvas

    monkeypatch.setattr(village_canvas, "tool_error", lambda value, **_: value)
    monkeypatch.setattr(village_canvas, "tool_result", lambda value, **_: value)


def _payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _project_db(tmp_path: Path, name: str = "data.db") -> Path:
    project_state = tmp_path / "state" / "alice" / "demo"
    project_state.mkdir(parents=True, exist_ok=True)
    path = project_state / name
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE shots (id INTEGER, name TEXT, video TEXT)")
    connection.execute(
        "INSERT INTO shots VALUES (1, '第 1 镜', NULL), (2, '第 2 镜', 'a.mp4')"
    )
    connection.commit()
    connection.close()
    return path


def test_statement_guard_allows_reads_and_blocks_writes():
    assert state_sql.validate_readonly_sql("select * from shots;").startswith("select")
    assert state_sql.validate_readonly_sql(
        "WITH missing AS (SELECT id FROM shots WHERE video IS NULL) SELECT * FROM missing"
    )

    blocked = {
        "select 1; select 2": "only one statement",
        "delete from shots": "only SELECT/WITH",
        "DROP TABLE shots": "only SELECT/WITH",
        "ATTACH 'other.db' AS other": "only SELECT/WITH",
        "PRAGMA query_only": "only SELECT/WITH",
        "": "sql is required",
    }
    for statement, expected in blocked.items():
        with pytest.raises(state_sql.StateSqlError) as excinfo:
            state_sql.validate_readonly_sql(statement)
        assert expected in str(excinfo.value)

    # A write smuggled into a CTE is caught by the keyword guard rather than by
    # the leading-word check.
    with pytest.raises(state_sql.StateSqlError) as excinfo:
        state_sql.validate_readonly_sql("WITH x AS (SELECT 1) DELETE FROM shots")
    assert "DELETE is not allowed" in str(excinfo.value)


def test_keywords_inside_literals_and_comments_stay_allowed():
    statement = "select 'delete from shots' as label, 1 as n -- update nothing"

    assert state_sql.validate_readonly_sql(statement) == statement


def test_only_allowlisted_databases_resolve(tmp_path):
    project_state = tmp_path / "state" / "alice" / "demo"
    resolved = state_sql.resolve_database_path(
        "project_data",
        project_state_dir=project_state,
        installation_state_dir=tmp_path / "state",
    )

    assert resolved == project_state / "data.db"
    assert state_sql.resolve_database_path(
        "durable_tasks",
        project_state_dir=project_state,
        installation_state_dir=tmp_path / "state",
    ) == tmp_path / "state" / "durable_project_tasks.sqlite3"

    # A project database without a project scope is unavailable, not guessed.
    assert (
        state_sql.resolve_database_path(
            "chat", installation_state_dir=tmp_path / "state"
        )
        is None
    )
    # The credential store is not addressable at all.
    for name in ("settings", "settings.db", "../settings.db", "state/local/settings.db"):
        with pytest.raises(state_sql.StateSqlError):
            state_sql.resolve_database_path(
                name,
                project_state_dir=project_state,
                installation_state_dir=tmp_path / "state",
            )


def test_database_catalogue_reports_availability(tmp_path):
    path = _project_db(tmp_path)
    project_state = path.parent

    entries = state_sql.list_databases(
        project_state_dir=project_state,
        installation_state_dir=tmp_path / "state",
    )
    by_name = {entry["name"]: entry for entry in entries}

    assert by_name["project_data"]["available"] is True
    assert by_name["project_data"]["size_bytes"] == path.stat().st_size
    assert by_name["chat"]["available"] is False
    assert all("settings" not in entry["name"] for entry in entries)


def test_schema_describe_lists_real_columns(tmp_path):
    path = _project_db(tmp_path)

    described = state_sql.describe_database(path)

    assert described["ok"] is True
    tables = {item["name"]: item for item in described["objects"]}
    assert tables["shots"]["type"] == "table"
    assert [column["name"] for column in tables["shots"]["columns"]] == [
        "id",
        "name",
        "video",
    ]
    assert described["truncated"] is False
    assert described["columns_truncated"] is False
    assert tables["shots"]["columns_truncated"] is False


def _per_table_reference(path: Path) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """The shape the schema read replaced: list objects, then one PRAGMA each.

    Kept in the test suite on purpose.  The single-statement version has to
    agree with this on every object name, and the only way to keep believing
    that is to compare against it on names designed to break a naive
    implementation.
    """

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        listed = connection.execute(
            "SELECT type, name FROM sqlite_master"
            " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            " ORDER BY type, name"
        ).fetchall()
        reference = []
        for kind, name in listed:
            quoted = str(name).replace('"', '""')
            columns = [
                (str(row[1]), str(row[2] or ""))
                for row in connection.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            ]
            reference.append((str(kind), str(name), columns))
        return reference
    finally:
        connection.close()


def test_schema_describe_survives_hostile_object_names(tmp_path):
    """No identifier quoting anywhere, so odd names must keep working.

    The schema read passes the object name to ``pragma_table_info`` as a bound
    value, not as interpolated SQL.  These names are the ones a quoting-based
    implementation gets wrong: an embedded double quote, an embedded single
    quote, a dot, a space, a dash, a percent sign, a reserved word, and a
    non-ASCII name.
    """

    path = tmp_path / "hostile.db"
    names = [
        "plain",
        "with space",
        "with.dot",
        'quote"inside',
        "single'quote",
        "select",
        "dash-name",
        "percent%name",
        "中文表名",
    ]
    connection = sqlite3.connect(str(path))
    try:
        for name in names:
            quoted = name.replace('"', '""')
            connection.execute(
                f'CREATE TABLE "{quoted}" (id INTEGER PRIMARY KEY, label TEXT, n REAL)'
            )
        connection.execute("CREATE VIEW v_plain AS SELECT id, label FROM plain")
        # Real internal objects, so the `NOT LIKE 'sqlite_%'` filter has
        # something to exclude instead of being untested.
        connection.execute(
            "CREATE TABLE autoinc (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"
        )
        connection.execute("INSERT INTO autoinc (v) VALUES ('x')")
        connection.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()

    described = state_sql.describe_database(path, max_tables=500)

    got = [
        (
            item["type"],
            item["name"],
            [(column["name"], column["type"]) for column in item["columns"]],
        )
        for item in described["objects"]
    ]
    assert got == _per_table_reference(path)
    assert {item["name"] for item in described["objects"]} >= set(names)
    assert not [name for name in (item["name"] for item in described["objects"]) if name.startswith("sqlite_")]


def test_schema_describe_caps_columns_per_object(tmp_path):
    """A 300-column table must not push the listing past a bounded size."""

    path = tmp_path / "wide.db"
    connection = sqlite3.connect(str(path))
    try:
        columns = ", ".join(f"c{index} TEXT" for index in range(300))
        connection.execute(f"CREATE TABLE wide (id INTEGER PRIMARY KEY, {columns})")
        connection.execute("CREATE TABLE narrow (a INTEGER, b TEXT)")
        connection.commit()
    finally:
        connection.close()

    described = state_sql.describe_database(path)
    by_name = {item["name"]: item for item in described["objects"]}

    assert len(by_name["wide"]["columns"]) == state_sql.MAX_COLUMNS_PER_OBJECT
    assert by_name["wide"]["columns_truncated"] is True
    # Columns keep their declared order, so the kept prefix is the useful part.
    assert [column["name"] for column in by_name["wide"]["columns"][:3]] == [
        "id",
        "c0",
        "c1",
    ]
    assert by_name["narrow"]["columns_truncated"] is False
    assert len(by_name["narrow"]["columns"]) == 2
    assert described["columns_truncated"] is True
    assert described["truncated"] is False
    assert described["read_mode"] == "single_statement"


def test_schema_describe_can_focus_one_table_and_lifts_the_column_cap(tmp_path):
    """A named table is a decided question, so it comes back whole.

    The per-object cap exists to keep a whole-database listing small.  Applied
    to a caller that already named the table it would only hide the answer, so
    asking by name raises the ceiling instead — that is what makes a truncated
    wide table recoverable rather than unknowable.
    """

    path = tmp_path / "wide.db"
    connection = sqlite3.connect(str(path))
    try:
        columns = ", ".join(f"c{index} TEXT" for index in range(300))
        connection.execute(f"CREATE TABLE wide (id INTEGER PRIMARY KEY, {columns})")
        connection.execute("CREATE TABLE other (a INTEGER)")
        connection.commit()
    finally:
        connection.close()

    focused = state_sql.describe_database(path, table="wide")

    assert [item["name"] for item in focused["objects"]] == ["wide"]
    assert len(focused["objects"][0]["columns"]) == 301
    assert focused["objects"][0]["columns_truncated"] is False
    assert focused["columns_truncated"] is False
    assert focused["truncated"] is False
    assert focused["read_mode"] == "single_statement"

    # The whole-file listing still caps that same table, so the two answers
    # differ on purpose rather than by accident.
    listing = state_sql.describe_database(path)
    wide = next(item for item in listing["objects"] if item["name"] == "wide")
    assert len(wide["columns"]) == state_sql.MAX_COLUMNS_PER_OBJECT
    assert wide["columns_truncated"] is True


def test_schema_describe_rejects_an_unknown_table_name(tmp_path):
    path = _project_db(tmp_path)

    with pytest.raises(state_sql.StateSqlError) as excinfo:
        state_sql.describe_database(path, table="not_a_table")

    assert "unknown table or view" in str(excinfo.value)


def test_schema_describe_survives_an_unreadable_view(tmp_path):
    """One broken view must not cost the caller every other object.

    SQLite happily stores a view over a missing table and then refuses to
    resolve it.  A single-statement read fails whole on such a view, so the read
    falls back to one PRAGMA per object, keeps the readable ones, and reports
    the broken one by name.  It also stays in ``objects``: dropping it would
    tell the caller the view does not exist.
    """

    path = tmp_path / "broken.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE shots (id INTEGER, video TEXT)")
        connection.execute("CREATE VIEW v_shots AS SELECT id FROM shots")
        connection.execute("CREATE VIEW v_broken AS SELECT * FROM no_such_table")
        connection.commit()
    finally:
        connection.close()

    described = state_sql.describe_database(path)
    by_name = {item["name"]: item for item in described["objects"]}

    assert described["read_mode"] == "per_object"
    assert set(by_name) == {"shots", "v_shots", "v_broken"}
    assert [column["name"] for column in by_name["shots"]["columns"]] == ["id", "video"]
    assert [column["name"] for column in by_name["v_shots"]["columns"]] == ["id"]
    assert by_name["v_broken"]["unreadable"] is True
    assert "no_such_table" in by_name["v_broken"]["error"]
    assert by_name["v_broken"]["columns"] == []
    assert described["unreadable_objects"] == [
        {
            "type": "view",
            "name": "v_broken",
            "error": "no such table: main.no_such_table",
        }
    ]
    assert described["object_count"] == 3

    # Naming the broken view asks the same question directly and answers the
    # same way, instead of failing the request.
    focused = state_sql.describe_database(path, table="v_broken")
    assert focused["objects"][0]["unreadable"] is True


def test_query_returns_rows_and_caps_them(tmp_path):
    path = _project_db(tmp_path)

    full = state_sql.run_readonly_query(
        path, "SELECT id, name FROM shots ORDER BY id"
    )
    assert full["columns"] == ["id", "name"]
    assert full["rows"] == [{"id": 1, "name": "第 1 镜"}, {"id": 2, "name": "第 2 镜"}]
    assert full["truncated"] is False

    capped = state_sql.run_readonly_query(
        path, "SELECT id FROM shots ORDER BY id", max_rows=1
    )
    assert capped["row_count"] == 1
    assert capped["row_truncated"] is True
    assert capped["truncated"] is True


def test_query_caps_bytes_and_summarizes_blobs(tmp_path):
    path = _project_db(tmp_path)
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE payloads (id INTEGER, body TEXT)")
    connection.execute(
        "INSERT INTO payloads VALUES (1, ?), (2, ?), (3, ?)",
        ("x" * 200, "y" * 200, "z" * 200),
    )
    connection.execute("CREATE TABLE blobs (data BLOB)")
    connection.execute("INSERT INTO blobs VALUES (x'0102030405')")
    connection.commit()
    connection.close()

    capped = state_sql.run_readonly_query(
        path, "SELECT body FROM payloads ORDER BY id", max_bytes=300
    )
    assert capped["row_count"] == 1
    assert capped["byte_truncated"] is True

    blobs = state_sql.run_readonly_query(path, "SELECT data FROM blobs")
    assert blobs["rows"] == [{"data": "<blob 5 bytes>"}]


def test_readonly_connection_refuses_writes(tmp_path):
    path = _project_db(tmp_path)
    before = path.read_bytes()
    connection = state_sql._connect_readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO shots VALUES (3, '第 3 镜', NULL)")
    finally:
        connection.close()

    assert path.read_bytes() == before


def test_query_deadline_interrupts_a_runaway_statement(tmp_path):
    path = _project_db(tmp_path)

    with pytest.raises(state_sql.StateSqlError) as excinfo:
        state_sql.run_readonly_query(
            path,
            "WITH RECURSIVE counter(x) AS ("
            "SELECT 1 UNION ALL SELECT x + 1 FROM counter) "
            "SELECT COUNT(*) FROM counter",
            timeout_seconds=0.2,
        )

    assert "time limit" in str(excinfo.value)


def test_missing_database_file_is_reported_not_created(tmp_path):
    project_state = tmp_path / "state" / "alice" / "demo"
    project_state.mkdir(parents=True)
    path = project_state / "workflow_runs.db"

    with pytest.raises(state_sql.StateSqlError) as excinfo:
        state_sql.run_readonly_query(path, "SELECT 1")

    assert "missing" in str(excinfo.value)
    assert path.exists() is False


def _route_app(tmp_path: Path):
    from fastapi import FastAPI
    from types import SimpleNamespace

    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import state as state_route

    app = FastAPI()
    app.include_router(state_route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}

    async def fake_scope(project, user, *, required_role="viewer"):
        return SimpleNamespace(state_dir=str(tmp_path / "state" / "alice" / "demo"))

    state_route.resolve_project_scope = fake_scope
    state_route._installation_state_dir = lambda: tmp_path / "state"
    return app


@pytest.mark.asyncio
async def test_state_routes_run_reads_and_refuse_writes(tmp_path):
    import httpx

    path = _project_db(tmp_path)
    app = _route_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/v1/state/databases", params={"project": "demo"})
        assert listed.status_code == 200
        names = [item["name"] for item in listed.json()["databases"]]
        assert "project_data" in names and not any("settings" in name for name in names)

        schema = await client.get(
            "/api/v1/state/schema", params={"database": "project_data", "project": "demo"}
        )
        assert schema.status_code == 200
        assert [item["name"] for item in schema.json()["objects"]] == ["shots"]

        queried = await client.post(
            "/api/v1/state/query",
            json={
                "database": "project_data",
                "sql": "SELECT id, video FROM shots WHERE video IS NULL",
                "project_id": "demo",
            },
        )
        assert queried.status_code == 200
        assert queried.json()["rows"] == [{"id": 1, "video": None}]

        rejected = await client.post(
            "/api/v1/state/query",
            json={
                "database": "project_data",
                "sql": "UPDATE shots SET video = 'x'",
                "project_id": "demo",
            },
        )
        assert rejected.status_code == 400
        assert "only SELECT/WITH" in rejected.json()["detail"]

        credentials = await client.post(
            "/api/v1/state/query",
            json={"database": "settings.db", "sql": "SELECT 1", "project_id": "demo"},
        )
        assert credentials.status_code == 400

    assert path.exists()


@pytest.mark.asyncio
async def test_state_schema_route_accepts_a_table_filter(tmp_path):
    """The route forwards the filter and reports a miss as a caller error."""

    import httpx

    _project_db(tmp_path)
    app = _route_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        focused = await client.get(
            "/api/v1/state/schema",
            params={"database": "project_data", "project": "demo", "table": "shots"},
        )
        assert focused.status_code == 200
        assert [item["name"] for item in focused.json()["objects"]] == ["shots"]
        assert focused.json()["read_mode"] == "single_statement"

        missing = await client.get(
            "/api/v1/state/schema",
            params={"database": "project_data", "project": "demo", "table": "nope"},
        )
        assert missing.status_code == 400
        assert "unknown table or view" in missing.json()["detail"]


def test_capability_index_exposes_the_state_sql_cards():
    import asyncio

    from novelvideo.agent_tools import village_canvas as plugin

    ids = {card["id"] for card in plugin._CAPABILITY_INDEX}

    assert {"state.sql.schema", "state.sql.query"} <= ids
    searched = asyncio.run(
        plugin._handle_capability_broker({"action": "search", "query": "只读 SQL 查询 表结构"})
    )
    assert {"state.sql.schema", "state.sql.query"} & {
        card["id"] for card in searched["capabilities"]
    }
    assert (
        plugin._capability_handler("state.sql.query").__name__ == "_handle_state_sql_query"
    )
    assert (
        plugin._capability_handler("state.sql.schema").__name__
        == "_handle_state_sql_schema"
    )


def test_state_sql_tools_call_exact_endpoints(monkeypatch):
    from novelvideo.agent_tools import village_canvas as plugin

    calls: list[tuple[str, str, dict]] = []

    def fake_request(method, path, *, query=None, body=None, timeout_seconds=None):
        calls.append((method, path, {"query": query, "body": body}))
        return {"ok": True}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_project_from_args", lambda args: "demo")

    schema_result = _payload(
        plugin._handle_state_sql_schema({"database": "project_data"})
    )
    query_result = _payload(
        plugin._handle_state_sql_query(
            {"database": "workflow_runs", "sql": "SELECT 1", "max_rows": 5}
        )
    )

    assert schema_result["ok"] is True and query_result["ok"] is True
    assert calls == [
        (
            "GET",
            "/api/v1/state/schema",
            {"query": {"database": "project_data", "project": "demo"}, "body": None},
        ),
        (
            "POST",
            "/api/v1/state/query",
            {
                "query": None,
                "body": {
                    "database": "workflow_runs",
                    "sql": "SELECT 1",
                    "project_id": "demo",
                    "max_rows": 5,
                },
            },
        ),
    ]


def test_state_sql_query_requires_sql_without_calling_the_api(monkeypatch):
    from novelvideo.agent_tools import village_canvas as plugin

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the API must not be called without sql")

    monkeypatch.setattr(plugin, "_request", explode)

    result = _payload(plugin._handle_state_sql_query({"database": "project_data"}))

    assert "sql is required" in str(result)


def test_state_sql_schema_tool_sends_the_table_filter_only_when_asked(monkeypatch):
    """`table` reaches the endpoint when the model names one, and not otherwise."""

    from novelvideo.agent_tools import village_canvas as plugin

    calls: list[dict] = []

    def fake_request(method, path, *, query=None, body=None, timeout_seconds=None):
        calls.append({"method": method, "path": path, "query": query})
        return {"ok": True}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_project_from_args", lambda args: "demo")

    plugin._handle_state_sql_schema({"database": "project_data", "table": "shots"})
    plugin._handle_state_sql_schema({"database": "project_data", "table": "   "})
    plugin._handle_state_sql_schema({"database": "project_data"})

    assert [call["query"] for call in calls] == [
        {"database": "project_data", "project": "demo", "table": "shots"},
        {"database": "project_data", "project": "demo"},
        {"database": "project_data", "project": "demo"},
    ]
    assert {call["path"] for call in calls} == {"/api/v1/state/schema"}
