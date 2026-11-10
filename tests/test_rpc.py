from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

from quantums.rpc import HttpQbitRpc, RpcError


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class HttpRpcTests(unittest.TestCase):
    def test_wallet_endpoint_auth_and_request_shape(self):
        seen = {}

        def open_request(request, timeout):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return _Response({"result": {"chain": "regtest"}, "error": None})

        rpc = HttpQbitRpc("http://node:18452/", "user", "pass", wallet="leap test")
        with patch("quantums.rpc.urlopen", open_request):
            self.assertEqual(rpc.call("getblockchaininfo"), {"chain": "regtest"})
        self.assertEqual(seen["url"], "http://node:18452/wallet/leap%20test")
        self.assertEqual(
            seen["auth"],
            "Basic " + base64.b64encode(b"user:pass").decode(),
        )
        self.assertEqual(seen["body"]["method"], "getblockchaininfo")
        self.assertEqual(seen["body"]["params"], [])

    def test_rpc_error_is_reported(self):
        rpc = HttpQbitRpc("http://node:18452")
        with patch(
            "quantums.rpc.urlopen",
            lambda request, timeout: _Response(
                {"result": None, "error": {"code": -1, "message": "no wallet"}}
            ),
        ):
            with self.assertRaisesRegex(RpcError, "no wallet"):
                rpc.call("getwalletinfo")


if __name__ == "__main__":
    unittest.main()
