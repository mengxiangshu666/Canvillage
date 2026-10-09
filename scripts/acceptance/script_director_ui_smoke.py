"""Isolated browser acceptance: no backend, credentials or private canvas writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5174')
    parser.add_argument('--output', type=Path, default=Path('workspace/artifacts/script-director-ui'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(args.base_url + '/src/__tests__/browser/script-director-preview.html')
            page.get_by_role('button', name='检查素材时长', exact=True).click()
            page.get_by_text('2s / 5s', exact=True).wait_for()
            assert page.get_by_text('3s / 5s', exact=True).count() == 1
            assert not page.get_by_role('button', name='逐镜出视频', exact=True).is_disabled()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            page.screenshot(path=str(args.output / f'{name}-duration-dialog.png'), full_page=True)
            page.get_by_role('button', name='取消', exact=True).click()
            page.get_by_role('button', name='检查摄影安排', exact=True).click()
            page.get_by_text('共 4 镜 · 1 镜需核对', exact=True).wait_for()
            assert page.get_by_text('摄影指令需确认', exact=True).count() == 1
            assert page.get_by_text('未识别摄影安排', exact=True).count() == 0
            assert page.get_by_text('无运镜', exact=True).count() == 0
            for camera in [
                '固定的中景背影机位，镜头始终跟随跑者奔跑，跑者起跑后镜头不再移动',
                '固定的中景背影机位，镜头跟随跑者奔跑，速度稳定，无切镜',
                '摄影机先沿岸边缓慢横移，落到倒影后停住',
                '固定在桌边，双人中景全程同框，保持结束构图',
            ]:
                assert page.get_by_text(camera, exact=True).get_attribute('title') == camera
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            page.screenshot(path=str(args.output / f'{name}-camera-dialog.png'), full_page=True)
            page.get_by_role('button', name='取消', exact=True).click()
            page.get_by_text('导演规划 · 1 段', exact=True).click()
            for label, value in [('空间调度', '沿平台向右，落点始终位于前方'), ('表演推进', '看清落点，犹豫后决定起跳，落地后松口气')]:
                sequence_field = page.get_by_label(f'第 1 段 · {label}', exact=True)
                sequence_field.fill(value)
                sequence_field.press('Tab')
                assert sequence_field.input_value() == value
            page.get_by_role('button', name='联合返工', exact=True).click()
            page.get_by_text('联合返工 · 高空挑战', exact=True).wait_for()
            page.get_by_placeholder('例如：把「她起身走向落地窗」改成「她停在原地，手指按住桌面，没有回头」').fill('先看清落点，再强调腾空与落地')
            page.screenshot(path=str(args.output / f'{name}-sequence-dialog.png'), full_page=True)
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            page.get_by_role('button', name='联合返工这一段', exact=True).click()
            assert page.get_by_label('段落返工要求').inner_text() == 'S1:先看清落点，再强调腾空与落地'
            field = page.get_by_label('故事承诺', exact=True)
            field.fill('一次关于勇气的冒险')
            field.press('Tab')
            page.get_by_text('导演规划已修改，已有镜头尚未同步', exact=True).wait_for()
            assert field.input_value() == '一次关于勇气的冒险'
            page.get_by_role('button', name='验证规划未同步阻断').click()
            assert page.get_by_label('制作阻断结果').inner_text() == '三项均阻断，节点与边未修改'
            page.get_by_role('button', name='播放预演').click()
            page.wait_for_timeout(2300)
            assert '镜 2' in page.locator('section').inner_text()
            page.get_by_role('button', name='暂停预演').click()
            page.get_by_role('button', name='回到开头').click()
            assert '镜 1' in page.locator('section').inner_text()
            image = page.locator('section img')
            assert image.evaluate('(img) => img.complete && img.naturalWidth === 640')
            original = image.get_attribute('src')
            page.get_by_role('button', name='替换测试分镜').click()
            assert image.get_attribute('src') != original
            assert image.evaluate('(img) => img.complete && img.naturalWidth === 640')
            page.screenshot(path=str(args.output / f'{name}-storyboard.png'), full_page=True)
            page.get_by_role('button', name='标记测试分镜过期').click()
            assert page.locator('section img').count() == 0
            assert '分镜已过期' in page.locator('section').inner_text()
            overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not overflow, f'{name}: horizontal overflow'
            assert not errors, errors
            page.screenshot(path=str(args.output / f'{name}.png'), full_page=True)
            results.append({'viewport': name, 'width': width, 'height': height, 'horizontal_overflow': overflow, 'page_errors': errors, 'camera_four_authored_rows_one_advisory': 'passed', 'sequence_rewrite_dialog_and_instruction': 'passed', 'editing': 'passed', 'playback': 'passed', 'storyboard_render_update_stale_refusal': 'passed', 'director_pending_three_actions_no_mutations': 'passed'})
            page.close()
        browser.close()
    (args.output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
