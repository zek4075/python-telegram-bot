#!/usr/bin/env python
#
#  A library that provides a Python interface to the Telegram Bot API
#  Copyright (C) 2015-2024
#  Leandro Toledo de Souza <devs@python-telegram-bot.org>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser Public License for more details.
#
#  You should have received a copy of the GNU Lesser Public License
#  along with this program.  If not, see [http://www.gnu.org/licenses/].
import asyncio
import contextlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import httpx
import pytest
from httpx import AsyncClient, AsyncHTTPTransport, Response

from telegram._utils.defaultvalue import DEFAULT_NONE
from telegram._utils.types import ODVInput
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.request import HTTPXRequest, RequestData


class NonchalantHttpxRequest(HTTPXRequest):
    """This Request class is used in the tests to suppress errors that we don't care about
    in the test suite.
    """

    async def _request_wrapper(
        self,
        method: str,
        url: str,
        request_data: Optional[RequestData] = None,
        read_timeout: ODVInput[float] = DEFAULT_NONE,
        connect_timeout: ODVInput[float] = DEFAULT_NONE,
        write_timeout: ODVInput[float] = DEFAULT_NONE,
        pool_timeout: ODVInput[float] = DEFAULT_NONE,
    ) -> bytes:
        try:
            return await super()._request_wrapper(
                method=method,
                url=url,
                request_data=request_data,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
            )
        except RetryAfter as e:
            pytest.xfail(f"Not waiting for flood control: {e}")
        except TimedOut as e:
            pytest.xfail(f"Ignoring TimedOut error: {e}")


async def expect_bad_request(func, message, reason):
    """
    Wrapper for testing bot functions expected to result in an :class:`telegram.error.BadRequest`.
    Makes it XFAIL, if the specified error message is present.

    Args:
        func: The awaitable to be executed.
        message: The expected message of the bad request error. If another message is present,
            the error will be reraised.
        reason: Explanation for the XFAIL.

    Returns:
        On success, returns the return value of :attr:`func`
    """
    try:
        return await func()
    except BadRequest as e:
        if message in str(e):
            pytest.xfail(f"{reason}. {e}")
        else:
            raise e


async def send_webhook_message(
    ip: str,
    port: int,
    payload_str: Optional[str],
    url_path: str = "",
    content_len: int = -1,
    content_type: str = "application/json",
    get_method: Optional[str] = None,
    secret_token: Optional[str] = None,
    unix: Optional[Path] = None,
) -> Response:
    headers = {
        "content-type": content_type,
    }
    if secret_token:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret_token

    if not payload_str:
        content_len = None
        payload = None
    else:
        payload = bytes(payload_str, encoding="utf-8")

    if content_len == -1:
        content_len = len(payload)

    if content_len is not None:
        headers["content-length"] = str(content_len)

    url = f"http://{ip}:{port}/{url_path}"

    transport = AsyncHTTPTransport(uds=unix) if unix else None

    async with AsyncClient(transport=transport) as client:
        return await client.request(
            url=url, method=get_method or "POST", data=payload, headers=headers
        )


_unblocked_request = httpx.AsyncClient.request


class RequestProtector:

    def __init__(self):
        self._requests_allowed: bool = True
        self._lock = contextlib.nullcontext()

    @property
    def requests_allowed(self) -> bool:
        return self._requests_allowed

    def allow_requests(self):
        with self._lock:
            self._requests_allowed = True

    def block_requests(self):
        with self._lock:
            self._requests_allowed = False

    @contextlib.contextmanager
    def allowing_requests(
        self: "RequestProtector",
    ) -> contextlib.AbstractAsyncContextManager["RequestProtector"]:
        with self._lock:
            orig_status = self._requests_allowed
            self._requests_allowed = True
            yield self
            self._requests_allowed = orig_status

    def build_request_method(self):
        async def request(*args, **kwargs):
            if not self._requests_allowed:
                raise RuntimeError("This function should not be called")
            return await _unblocked_request(*args, **kwargs)

        return request


REQUEST_PROTECTOR = RequestProtector()
