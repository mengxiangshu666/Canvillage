"""Isolated asset review fixture; no private canvas or provider operation."""
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    output = Path('workspace/artifacts/script-asset-review')
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5174/src/__tests__/browser/script-asset-review.html')
            page.get_by_role('button', name='审看 滑板').click()
            image = page.get_by_role('img', name='滑板')
            image.wait_for()
            assert image.evaluate('(i) => i.complete && i.naturalWidth === 640')
            page.get_by_label('资产画面问题').fill('板面暗部噪点，保留材质后重做')
            page.get_by_role('button', name='退回重做', exact=True).click()
            page.get_by_text('画面需重做', exact=True).wait_for()
            page.get_by_role('button', name='审看 滑板').click()
            assert page.get_by_label('资产画面问题').input_value() == '板面暗部噪点，保留材质后重做'
            for label in ['无随机噪点、压缩块、摩尔纹', '身份与风格一致', '结构和材质细节正确']:
                page.get_by_role('checkbox', name=label, exact=True).check()
            page.get_by_role('checkbox', name='主视图', exact=True).check()
            page.screenshot(path=str(output / f'{name}.png'), full_page=True)
            page.get_by_role('button', name='确认画面', exact=True).click()
            page.get_by_text('人工画面确认', exact=True).wait_for()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            print(name, 'passed: image decoded, rejection/acceptance and notes retained, no overflow/errors')
            page.close()
        browser.close()


if __name__ == '__main__':
    main()
