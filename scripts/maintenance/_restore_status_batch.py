"""One-off: put the over-evicted 2026-09-19 batch back into STATUS.md."""

from __future__ import annotations

import pathlib


STATUS = pathlib.Path("STATUS.md")
ARCHIVE = pathlib.Path("docs/status/STATUS-ARCHIVE-2026-08-and-earlier.md")
MARKER = "## 2026-09-19 启动时终态回执自愈".encode()
IDENTITY = "## 项目身份".encode()


def sections(data: bytes) -> tuple[int, int]:
    lines = data.splitlines()
    total = sum(1 for line in lines if line.startswith(b"## "))
    dated = sum(
        1
        for line in lines
        if line.startswith(b"## ")
        and len(line) > 13
        and line[3:7].isdigit()
        and line[7:8] == b"-"
    )
    return total, dated


def main() -> int:
    archive = ARCHIVE.read_bytes()
    start = archive.index(MARKER)
    block = archive[start:]
    ARCHIVE.write_bytes(archive[:start].rstrip(b"\r\n") + b"\r\n")

    status = STATUS.read_bytes()
    lines = status.splitlines(keepends=True)
    identity = next(
        index for index, line in enumerate(lines) if line.startswith(IDENTITY)
    )
    # [last text][blank][---][blank][## 项目身份]
    insert_at = identity - 2
    assert lines[insert_at].startswith(b"---"), lines[insert_at]
    status = b"".join(lines[:insert_at] + block.splitlines(keepends=True) + [b"\r\n"] + lines[insert_at:])
    STATUS.write_bytes(status)

    print("STATUS.md lines:", len(status.splitlines()), "sections:", sections(status))
    print(
        "archive lines:",
        len(ARCHIVE.read_bytes().splitlines()),
        "sections:",
        sections(ARCHIVE.read_bytes()),
    )

    sizes: list[tuple[int, bytes]] = []
    current: list[bytes] = []
    for line in status.splitlines():
        if line.startswith(b"## "):
            if current:
                sizes.append((len(current), current[0]))
            current = [line]
        elif current:
            current.append(line)
    if current:
        sizes.append((len(current), current[0]))
    entry_sizes = [
        (size, title)
        for size, title in sizes
        if len(title) > 13 and title[3:7].isdigit() and title[7:8] == b"-"
    ]
    print("dated entries:", len(entry_sizes))
    print("entry lines min/avg/max:", min(s for s, _ in entry_sizes), sum(s for s, _ in entry_sizes) // len(entry_sizes), max(s for s, _ in entry_sizes))
    print("largest:")
    for size, title in sorted(entry_sizes, reverse=True)[:5]:
        print("   ", size, title.decode("utf-8")[:60])
    fixed = len(status.splitlines()) - sum(s for s, _ in entry_sizes)
    print("non-entry lines (header/archive index/reference):", fixed)
    print("projected 30 entries at avg:", fixed + 30 * (sum(s for s, _ in entry_sizes) // len(entry_sizes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
