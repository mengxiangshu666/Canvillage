"""Exercise real local overlapping audio elements without APIs or private data."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        for width, height in [(1280, 900), (390, 844)]:
            page = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5174/src/__tests__/browser/compose-audio-preview.html')
            page.wait_for_function("[...document.querySelectorAll('audio')].length === 3 && [...document.querySelectorAll('audio')].every(el => el.readyState >= 1)")
            page.get_by_role('button', name='播放重叠声音').click()
            page.wait_for_function("[...document.querySelectorAll('audio')].every(el => !el.paused && el.currentTime > 0.6)")
            playing = page.locator('audio').evaluate_all('(els) => els.map(el => ({ paused: el.paused, time: el.currentTime, volume: el.volume, muted: el.muted }))')
            page.get_by_role('button', name='修改首段声音').click()
            page.wait_for_function("document.querySelector('audio').muted && document.querySelector('audio').volume === 0.2 && document.querySelector('audio').playbackRate === 1.5")
            page.get_by_role('button', name='移到片尾').click()
            page.wait_for_function("[...document.querySelectorAll('audio')].every(el => el.paused && !el.hasAttribute('src'))")
            assert not errors, errors
            results.append({'viewport': [width, height], 'simultaneous_playback': playing, 'live_settings': 'passed', 'end_stop': 'passed', 'page_errors': errors})
            page.close()
        browser.close()
    output = Path('workspace/artifacts/compose-audio-preview-20261007')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
