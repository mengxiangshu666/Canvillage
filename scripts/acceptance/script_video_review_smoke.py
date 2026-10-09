"""Synthetic video review browser check; no provider or private canvas access."""
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    output = Path('workspace/artifacts/script-video-review')
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5174/src/__tests__/browser/script-video-review.html')
            page.get_by_role('button', name='播放镜 2 版本 1').wait_for()
            video = page.locator('video')
            video.evaluate('(v) => v.play()')
            page.wait_for_timeout(350)
            assert video.evaluate('(v) => v.videoWidth === 640 && v.currentTime > 0')
            pixels = video.evaluate('''(v) => {
                const c = document.createElement('canvas'); c.width = 640; c.height = 360;
                const ctx = c.getContext('2d'); ctx.drawImage(v, 0, 0);
                return Array.from(ctx.getImageData(10, 10, 1, 1).data);
            }''')
            assert pixels[1] > 50, pixels
            page.get_by_role('button', name='播放镜 2 版本 1').click()
            assert page.get_by_label('镜 2 视频').count() == 1
            assert video.count() == 1
            video.evaluate('(v) => { v.pause(); v.currentTime = 0.5; }')
            page.locator('summary').filter(has_text='审看意见').click()
            page.get_by_label('看到的问题', exact=True).fill('暗部噪点逐帧跳动，毛发边缘闪烁')
            page.get_by_label('观看感受', exact=True).fill('干扰观看角色表演')
            page.get_by_label('希望怎么修', exact=True).fill('保留毛发细节，稳定暗部和边缘')
            page.get_by_role('button', name='保存意见', exact=True).click()
            page.get_by_text('0.50s · 噪点与纹理闪烁：暗部噪点逐帧跳动，毛发边缘闪烁', exact=True).wait_for()
            page.get_by_role('button', name='返工这一镜', exact=True).click()
            page.get_by_text('只改第 2 镜', exact=True).wait_for()
            page.get_by_role('button', name='取消', exact=True).click()
            page.get_by_role('button', name='返工镜 2', exact=True).click()
            page.get_by_text('只改第 2 镜', exact=True).wait_for()
            assert page.get_by_text('进入下一平台后放慢速度并回望', exact=True).count() == 2
            page.get_by_role('button', name='取消', exact=True).click()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            page.screenshot(path=str(output / f'{name}.png'), full_page=True)
            print(name, 'passed: moving pixels, version/time-bound note, revision entry, no overflow/errors')
            page.close()
        browser.close()


if __name__ == '__main__':
    main()
