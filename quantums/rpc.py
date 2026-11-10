from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass, replace
import json
from pathlib import Path
import subprocess
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .networks import NETWORKS


class RpcError(RuntimeError):
    pass


class Rpc(Protocol):
    def call(self, method: str, *params: Any) -> Any: ...

    def for_wallet(self, wallet: str | None) -> "Rpc": ...


def require_network(info: dict[str, Any], expected: str) -> None:
    actual = info.get("chain")
    if expected not in NETWORKS or actual != expected:
        raise RpcError(f"Qbit RPC network mismatch: requested={expected}, node={actual}")


@dataclass
class NetworkBoundRpc:
    """Check the node before any wallet access; refresh on each tip query."""

    transport: Rpc
    chain: str
    _verified: bool = False

    def for_wallet(self, wallet: str | None) -> "NetworkBoundRpc":
        return NetworkBoundRpc(self.transport.for_wallet(wallet), self.chain)

    def call(self, method: str, *params: Any) -> Any:
        if method == "getblockchaininfo" or not self._verified:
            self._verified = False
            public = self.transport.for_wallet(None)
            info = public.call("getblockchaininfo")
            require_network(info, self.chain)
            self._verified = True
            if method == "getblockchaininfo" and not params:
                return info
        return self.transport.call(method, *params)


@dataclass(frozen=True)
class QbitRpc:
    """qbit-cli transport, retained for local diagnostics and compatibility."""

    cli: str = "qbit-cli"
    chain: str = "main"
    datadir: str | None = None
    wallet: str | None = None

    def for_wallet(self, wallet: str | None) -> "QbitRpc":
        return replace(self, wallet=wallet)

    def command(self) -> list[str]:
        command = [self.cli, f"-chain={self.chain}"]
        if self.datadir:
            command.append(f"-datadir={self.datadir}")
        if self.wallet:
            command.append(f"-rpcwallet={self.wallet}")
        return command

    def call(self, method: str, *params: Any) -> Any:
        command = self.command() + [method]
        command.extend(
            value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
            for value in params
        )
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RpcError(f"{method} failed: {detail}")
        output = completed.stdout.strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output


@dataclass(frozen=True)
class HttpQbitRpc:
    """Minimal dependency-free JSON-RPC transport for Qbit Core and Umbrel."""

    url: str
    username: str | None = None
    password: str | None = None
    cookie_file: Path | None = None
    wallet: str | None = None
    timeout: float = 30.0

    def for_wallet(self, wallet: str | None) -> "HttpQbitRpc":
        return replace(self, wallet=wallet)

    def _credentials(self) -> tuple[str, str] | None:
        if self.username is not None or self.password is not None:
            if self.username is None or self.password is None:
                raise RpcError("RPC username and password must be supplied together")
            return self.username, self.password
        if self.cookie_file is None:
            return None
        try:
            user, password = self.cookie_file.read_text(encoding="utf-8").strip().split(":", 1)
        except (OSError, ValueError) as exc:
            raise RpcError(f"cannot read RPC cookie {self.cookie_file}") from exc
        return user, password

    def _endpoint(self) -> str:
        base = self.url.rstrip("/")
        return base if self.wallet is None else f"{base}/wallet/{quote(self.wallet, safe='')}"

    def call(self, method: str, *params: Any) -> Any:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": "leap", "method": method, "params": list(params)},
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        credentials = self._credentials()
        if credentials is not None:
            token = b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = Request(self._endpoint(), data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error")
            except Exception:
                detail = f"HTTP {exc.code}"
            raise RpcError(f"{method} failed: {detail}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RpcError(f"{method} failed: {exc}") from exc
        error = payload.get("error")
        if error is not None:
            message = error.get("message", error) if isinstance(error, dict) else error
            raise RpcError(f"{method} failed: {message}")
        return payload.get("result")
