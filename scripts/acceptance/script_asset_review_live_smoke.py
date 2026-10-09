"""Exercise asset review through deployed UI/autosave in a disposable project."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
CLI = [str(ROOT / 'runtime/python/python.exe'), str(ROOT / 'village_canvas_cli.py')]


def command(*args: str, body: dict | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        if body is not None:
            path = Path(directory) / 'body.json'
            path.write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
            args = (*args, '--body', '@' + str(path))
        result = subprocess.run([*CLI, *args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)


def main() -> None:
    project = ''
    output = ROOT / 'workspace/artifacts/script-asset-review-live'
    output.mkdir(parents=True, exist_ok=True)
    try:
        created = command('api', 'post', '/projects', body={'name': 'acceptance_review_' + uuid.uuid4().hex[:12]})
        project = created['data']['id']
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome')
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            bitmap = page.evaluate('''() => {
                const c = document.createElement('canvas'); c.width = 640; c.height = 360;
                const x = c.getContext('2d'); x.fillStyle = '#eee'; x.fillRect(0,0,640,360);
                x.fillStyle = '#168a82'; x.fillRect(90,130,460,65);
                return c.toDataURL();
            }''')
            definition = {'schema': 'village.script-asset-definition.v1', 'asset_id': 'prop:滑板',
                          'role': 'prop', 'name': '滑板', 'description': '青色板面',
                          'source_image_url': '', 'identity_locks': ['content_hash'], 'dependencies': []}
            digest = hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            canvas_id = 'review_acceptance'
            payload = {'canvas_id': canvas_id, 'nodes': [
                {'id': 'script', 'type': 'scriptNode', 'position': {'x': 0, 'y': 0},
                 'style': {'width': 800, 'height': 600}, 'data': {'activeViewId': 'asset',
                 'scriptResult': {'rows': [{'shot_no': 1, 'prop_tags': '滑板', 'prop_descriptions': {'滑板': '青色板面'}}]}}},
                {'id': 'asset', 'type': 'imageGenNode', 'position': {'x': 900, 'y': 0},
                 'data': {'imageUrl': bitmap, 'scriptAssetId': 'prop:滑板', 'scriptAssetOwnerId': 'script',
                          'scriptAssetRevision': 1, 'scriptAssetContentHash': digest, 'scriptAssetIdentityLocks': ['content_hash']}},
            ], 'edges': [], 'viewport': {'x': 180, 'y': 180, 'zoom': 0.8}}
            path = f'/projects/{project}/freezone/canvases/{canvas_id}'
            command('--project', project, 'api', 'put', path, body=payload)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:8784/projects/{project}/freezone?canvas={canvas_id}')
            page.get_by_role('button', name='审看 滑板').click(timeout=60000)
            page.get_by_role('img', name='滑板', exact=True).wait_for()
            page.get_by_label('资产画面问题').fill('测试暗部噪点，保留板面材质')
            with page.expect_response(lambda response: '/freezone/canvases/' in response.url and response.request.method == 'PUT' and response.status == 200, timeout=30000):
                page.get_by_role('button', name='退回重做', exact=True).click()
            restored = command('--project', project, 'api', 'get', path)['data']
            receipt = next(node for node in restored['nodes'] if node['id'] == 'asset')['data']['scriptAssetVisualReview']
            assert receipt['status'] == 'blocked'
            assert receipt['ownerId'] == 'script' and receipt['assetId'] == 'prop:滑板'
            assert receipt['notes'] == '测试暗部噪点，保留板面材质'
            page.reload()
            page.get_by_role('button', name='审看 滑板').click(timeout=60000)
            assert page.get_by_label('资产画面问题').input_value() == receipt['notes']
            page.screenshot(path=str(output / 'reloaded.png'), full_page=True)
            for label in ['无随机噪点、压缩块、摩尔纹', '身份与风格一致', '结构和材质细节正确']:
                page.get_by_role('checkbox', name=label, exact=True).check()
            page.get_by_role('checkbox', name='主视图', exact=True).check()
            with page.expect_response(lambda response: '/freezone/canvases/' in response.url and response.request.method == 'PUT' and response.status == 200, timeout=30000):
                page.get_by_role('button', name='确认画面', exact=True).click()
            accepted = command('--project', project, 'api', 'get', path)['data']
            accepted_receipt = next(node for node in accepted['nodes'] if node['id'] == 'asset')['data']['scriptAssetVisualReview']
            assert accepted_receipt['status'] == 'passed'
            assert all(accepted_receipt['checks'].values())
            assert accepted_receipt['views']
            assert accepted['revision'] > restored['revision']
            page.reload()
            page.get_by_role('button', name='审看 滑板').click(timeout=60000)
            assert page.get_by_role('checkbox', name='无随机噪点、压缩块、摩尔纹', exact=True).is_checked()
            assert page.get_by_role('checkbox', name='主视图', exact=True).is_checked()
            page.screenshot(path=str(output / 'accepted-reloaded.png'), full_page=True)
            assert not errors, errors
            (output / 'result.json').write_text(json.dumps({'project': project, 'revision': restored.get('revision'), 'receipt': receipt, 'accepted_revision': accepted['revision'], 'accepted_receipt': accepted_receipt, 'page_errors': errors, 'paid_generation': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            browser.close()
            print('passed: deployed browser PUT, CLI readback and reload retain current review')
    finally:
        if project:
            command('--project', project, 'api', 'post', f'/projects/{project}/delete')
            command('--project', project, 'api', 'post', f'/projects/{project}/purge')
            print('disposable project purged:', project)


if __name__ == '__main__':
    main()
