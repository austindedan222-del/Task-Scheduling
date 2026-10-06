"""
Run this once (with internet access) to download Bootstrap + Bootstrap
Icons into scheduler/static/scheduler/vendor/, so the app no longer
depends on a CDN at runtime.

Why this matters for a panel demo: a CDN link only loads if the room
has working internet at that moment. These files, once downloaded, are
committed to your project like any other static file and load instantly
from your own machine every time, demo or not.

Usage:
    python download_assets.py

Re-run any time to re-download (it just overwrites the same files).
"""

import os
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(BASE_DIR, 'scheduler', 'static', 'scheduler', 'vendor')

FILES = [
    # (download URL, path relative to VENDOR_DIR)
    (
        'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
        os.path.join('bootstrap', 'bootstrap.min.css'),
    ),
    (
        'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css',
        os.path.join('bootstrap-icons', 'bootstrap-icons.css'),
    ),
    # The icon font's CSS references these two font files via a relative
    # "./fonts/..." path, so they must sit in a fonts/ folder right next
    # to bootstrap-icons.css, not loose in the vendor folder.
    (
        'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2',
        os.path.join('bootstrap-icons', 'fonts', 'bootstrap-icons.woff2'),
    ),
    (
        'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff',
        os.path.join('bootstrap-icons', 'fonts', 'bootstrap-icons.woff'),
    ),
]


def main():
    print('Downloading Bootstrap + Bootstrap Icons for offline use...\n')
    for url, rel_path in FILES:
        dest = os.path.join(VENDOR_DIR, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        print(f'  {rel_path} ...', end=' ')
        try:
            urllib.request.urlretrieve(url, dest)
            size_kb = os.path.getsize(dest) / 1024
            print(f'done ({size_kb:.0f} KB)')
        except Exception as exc:
            print(f'FAILED — {exc}')
            print(f'    Try downloading this URL manually and saving it to:\n    {dest}')

    print('\nDone. base.html already points at these local files — just')
    print('refresh your browser (Ctrl+Shift+R) to pick them up.')


if __name__ == '__main__':
    main()
