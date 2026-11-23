"""ローカル API の保護

サーバーは 127.0.0.1 だけで待ち受けるが、ブラウザーで開いた外部のページからも要求は届く（CORS は応答を読ませない
だけで、処理の実行は止めない）。自分の画面以外からの要求を、処理の前に断る。

- Host: 自分の宛先（127.0.0.1 / localhost と、使っているポート）だけを受け付ける（DNS リバインディング対策）
- Origin: 付いているときは、自分の画面のものだけを受け付ける
- Origin が無いとき: ブラウザーが付ける Sec-Fetch-Site で、他のサイトからの書き込みと埋め込みを断る
  （画面の切り替えと、ブラウザー以外からの要求（起動待ちなど）は通す）
"""
import logging
from typing import List, Optional
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

logger = logging.getLogger("voice2doc")

_SAFE_METHODS = {"GET", "HEAD"}
_OTHER_SITES = {"cross-site", "same-site"}


class LocalGuard:
    """自分の画面以外からの要求を断る ASGI ミドルウェア（一番外側に置く）"""

    def __init__(self, app, origins: List[str]):
        self.app = app
        self.origins = set(origins)
        self.hosts = {urlsplit(o).netloc for o in origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            status = self._reject_status(scope)
            if status is not None:
                logger.warning(
                    f"自分の画面以外からの要求を断りました: {status} {scope['method']} {scope['path'][:100]!r}"
                )
                await JSONResponse({"detail": "Forbidden"}, status_code=status)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _reject_status(self, scope) -> Optional[int]:
        """断るときの HTTP ステータス（受け付けるときは None）"""
        def values(name: bytes) -> List[str]:
            return [v.decode("latin-1") for k, v in scope["headers"] if k == name]

        hosts = values(b"host")
        if len(hosts) != 1 or hosts[0].lower() not in self.hosts:
            return 400

        origins = values(b"origin")
        if origins:
            return None if len(origins) == 1 and origins[0] in self.origins else 403

        if set(values(b"sec-fetch-site")) & _OTHER_SITES:
            navigation = values(b"sec-fetch-mode") == ["navigate"] and values(b"sec-fetch-dest") == ["document"]
            if scope["method"] not in _SAFE_METHODS or not navigation:
                return 403
        return None
