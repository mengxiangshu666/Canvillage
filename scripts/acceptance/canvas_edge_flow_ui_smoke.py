"""Exercise real flow controls and SVG motion without accessing user canvases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def set_slider(page, label: str, value: float) -> None:
    slider = page.get_by_role('slider', name=label)
    minimum = float(slider.get_attribute('min'))
    step = float(slider.get_attribute('step'))
    slider.press('Home')
    for _ in range(round((value - minimum) / step)):
        slider.press('ArrowRight')
    expect(slider).to_have_value(str(value))


def motion_positions(page) -> list[float]:
    return page.locator('.canvas-data-edge__packet').evaluate_all(
        '(groups) => [groups[0].getCTM().e, groups[3].getCTM().e]'
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5176')
    parser.add_argument('--output', type=Path, default=Path('.cache/canvas-edge-flow-ui'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors, api_requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def refuse_api(route):
                api_requests.append(route.request.url.split('?')[0])
                route.abort()

            page.route('**/api/v1/**', refuse_api)
            page.goto(args.base_url + '/src/__tests__/browser/canvas-edge-flow-preview.html')
            packets = page.locator('.canvas-data-edge__packet')
            expect(packets).to_have_count(6)
            expect(page.locator('animateMotion').first).to_have_attribute('dur', '7s')
            page.get_by_role('button', name='连线色彩：樱粉', exact=True).click()
            expect(page.locator('linearGradient stop').first).to_have_attribute('stop-color', '#f472b6')
            page.get_by_role('button', name='流光色彩：极光青', exact=True).click()
            page.get_by_role('button', name='连线色彩：翠绿', exact=True).click()
            expect(page.locator('linearGradient stop').first).to_have_attribute('stop-color', '#22d3ee')
            expect(page.locator('.react-flow__edge-path').first).to_have_css('stroke', 'rgba(74, 222, 128, 0.82)')
            set_slider(page, '流动速度', 2)
            set_slider(page, '光点大小', 1.5)
            set_slider(page, '光点粗细', 8)
            expect(page.locator('animateMotion').first).to_have_attribute('dur', '3.5s')
            expect(page.locator('animateMotion').nth(1)).to_have_attribute('begin', f'{-3.5 / 3}s')
            expect(packets.first.locator('g')).to_have_attribute('transform', f'scale({0.45 * 1.5}, 1)')
            expect(packets.first.locator('line').last).to_have_attribute('stroke-width', '8')
            expect(packets.first.locator('line').first).to_have_attribute('stroke-width', '24')

            # Both paths finish in one cycle: the longer path must move farther per frame.
            lengths = page.locator('defs > path').evaluate_all(
                '(paths) => paths.map(path => path.getTotalLength() * path.getCTM().a)'
            )
            before = motion_positions(page)
            page.wait_for_timeout(150)
            after = motion_positions(page)
            moved = [(end - start) % length for start, end, length in zip(before, after, lengths)]
            assert moved[0] > 0 and moved[1] > moved[0], moved
            assert abs(moved[0] / lengths[0] - moved[1] / lengths[1]) < 0.02, (moved, lengths)

            saved = page.evaluate("JSON.parse(localStorage.getItem('canvas.viewSettings.v1'))")
            assert saved['edgeFlowColor'] == '#22d3ee'
            assert (saved['edgeFlowSpeed'], saved['edgeFlowSize'], saved['edgeFlowWidth']) == (2, 1.5, 8)
            page.reload()
            expect(page.locator('animateMotion').first).to_have_attribute('dur', '3.5s')
            expect(page.get_by_label('自定义流光色彩')).to_have_value('#22d3ee')
            expect(page.get_by_role('slider', name='光点大小')).to_have_value('1.5')
            expect(page.get_by_role('slider', name='光点粗细')).to_have_value('8')
            page.get_by_role('slider', name='光点粗细').scroll_into_view_if_needed()
            overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not overflow
            page.screenshot(path=str(args.output / f'{name}-custom.png'), full_page=True)

            page.get_by_role('button', name='暂停交互流光', exact=True).click()
            expect(packets).to_have_count(0)
            page.get_by_role('button', name='恢复交互流光', exact=True).click()
            expect(packets).to_have_count(6)
            page.emulate_media(reduced_motion='reduce')
            expect(packets.first).to_be_hidden()
            page.emulate_media(reduced_motion='no-preference')
            page.get_by_role('switch', name='流光跟随连线颜色').click()
            expect(page.locator('linearGradient stop').first).to_have_attribute('stop-color', '#4ade80')
            page.get_by_role('button', name='恢复默认', exact=True).click()
            expect(page.locator('animateMotion').first).to_have_attribute('dur', '7s')
            expect(page.get_by_role('slider', name='光点大小')).to_have_value('1')
            expect(page.get_by_role('slider', name='光点粗细')).to_have_value('4')
            assert not errors, errors
            assert not api_requests, api_requests
            results.append({
                'viewport': name, 'horizontal_overflow': overflow, 'page_errors': errors,
                'api_requests': api_requests, 'independent_color_and_controls': 'passed',
                'reload_reset_motion_suppression_reduced_motion': 'passed',
                'path_lengths_screen_px': lengths, 'motion_screen_px': moved,
                'longer_path_moves_faster_same_cycle': 'passed',
            })
            page.close()
        browser.close()
    (args.output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
