from __future__ import annotations


class BrowserFetchingDisabled(RuntimeError):
    pass


def unsupported_browser_fetch(*_: object, **__: object) -> None:
    raise BrowserFetchingDisabled(
        "Browser automation, fingerprint spoofing, CAPTCHA solving, and challenge bypass are "
        "outside the compliance boundary for this project."
    )
