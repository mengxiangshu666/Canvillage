"""前端源码入口不能被 dist 构建产物污染。"""

from pathlib import Path


def test_vite_index_uses_source_entry():
    index_html = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
    html = index_html.read_text(encoding="utf-8")

    assert '<script type="module" src="/src/main.tsx"></script>' in html
    assert 'src="/assets/index-' not in html
    assert 'href="/assets/index-' not in html
