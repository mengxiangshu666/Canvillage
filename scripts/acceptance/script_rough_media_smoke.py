"""Synthetic mixed rough preview; no models or private canvas access."""
from pathlib import Path
import subprocess

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def main():
    media = ROOT / 'frontend/src/__tests__/browser/rough-preview-test.webm'
    output = ROOT / 'workspace/artifacts/script-rough-media'
    output.mkdir(parents=True, exist_ok=True)
    if media.exists():
        raise RuntimeError('Temporary media path already exists; refusing to overwrite')
    try:
        subprocess.run([str(ROOT / 'runtime/ffmpeg/ffmpeg.exe'), '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=640x360:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440',
                        '-t', '3', '-c:v', 'libvpx', '-pix_fmt', 'yuv420p', '-c:a', 'libopus', str(media)],
                       check=True, timeout=60)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel='chrome')
            for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:5174/src/__tests__/browser/script-rough-media.html')
                video = page.locator('video')
                page.wait_for_function('document.querySelector("video")?.readyState >= 2')
                assert video.evaluate('(v) => Math.abs(v.duration - 3) < 0.1 && v.muted')
                page.get_by_role('button', name='开启素材声音').click()
                assert video.evaluate('(v) => !v.muted')
                page.get_by_role('button', name='播放预演').click()
                page.wait_for_function('document.querySelector("video")?.currentTime > 0.2')
                assert video.evaluate('''v => {
                    const c = document.createElement('canvas'); c.width=640; c.height=360;
                    const ctx=c.getContext('2d'); ctx.drawImage(v,0,0);
                    return ctx.getImageData(10,10,1,1).data.slice(0,3).some(x=>x>20);
                }''')
                page.get_by_text('中间镜头缺视频', exact=True).wait_for()
                page.screenshot(path=str(output / f'{name}.png'), full_page=True)
                page.get_by_label('预演第 3 镜').wait_for()
                page.get_by_role('button', name='播放预演').wait_for()
                assert float(page.get_by_role('slider').input_value()) == 3
                page.get_by_role('slider').fill('0.5')
                page.wait_for_function('Math.abs(document.querySelector("video").currentTime - 0.5) < 0.1')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                assert not errors, errors
                print(name, 'passed: decoded moving video/audio toggle, cut/static/cut, stop, seek, no overflow/errors')
                page.close()
            browser.close()
    finally:
        media.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
