"""Explorer client implementations for supported blockchain networks."""

from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Iterable, Mapping, Sequence

import httpx

from archeblow_service import Network, TransactionHop
from api_keys import get_api_key


class ExplorerAPIError(RuntimeError):
    """Raised when a blockchain explorer request fails."""


class UnsupportedNetworkError(ExplorerAPIError):
    """Raised when no explorer implementation exists for a network."""


class _BaseExplorerClient:
    """Common helper base for explorer clients."""

    def __init__(
        self,
        network: Network,
        *,
        session: httpx.AsyncClient | None = None,
        service_id: str,
        display_name: str,
    ) -> None:
        self.network = network
        self.service_id = service_id
        self.service_name = display_name
        self._session = session

    async def _request_json(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        close_session = False
        session = self._session
        if session is None:
            timeout = httpx.Timeout(20.0, connect=10.0, read=20.0)
            session = httpx.AsyncClient(timeout=timeout)
            close_session = True
        try:
            response = await session.get(url, params=params, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network errors handled at runtime
            raise ExplorerAPIError(
                f"API запрос завершился ошибкой {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:  # pragma: no cover - network errors handled at runtime
            raise ExplorerAPIError("Ошибка сети при обращении к публичному API") from exc
        finally:
            if close_session:
                await session.aclose()
        data = response.json()
        if not isinstance(data, Mapping):
            raise ExplorerAPIError("Некорректный ответ от API: ожидался объект JSON")
        return data


class BlockchainComExplorerClient(_BaseExplorerClient):
    """Explorer powered by blockchain.com for Bitcoin addresses."""

    _BASE_URLS: Mapping[Network, str] = {
        Network.BITCOIN: "https://blockchain.info",
    }

    def __init__(
        self,
        network: Network,
        *,
        session: httpx.AsyncClient | None = None,
        api_code: str | None = None,
    ) -> None:
        super().__init__(
            network,
            session=session,
            service_id="blockchain_com",
            display_name="Blockchain.com Explorer",
        )
        self._api_code = api_code

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        base_url = self._BASE_URLS.get(self.network)
        if base_url is None:
            raise UnsupportedNetworkError(
                f"Сеть {self.network.value} не поддерживается blockchain.com API."
            )
        url = f"{base_url}/rawaddr/{address}"
        params: dict[str, object] = {"limit": 50}
        if self._api_code:
            params["api_code"] = self._api_code
        payload = await self._request_json(url, params=params)
        txs = payload.get("txs", [])
        if not isinstance(txs, Iterable):
            return []
        hops: list[TransactionHop] = []
        for tx_obj in txs:
            if not isinstance(tx_obj, Mapping):
                continue
            tx_hash = str(tx_obj.get("hash") or "")
            timestamp = _coerce_timestamp(tx_obj.get("time"))
            block_height = tx_obj.get("block_height")
            inputs = tx_obj.get("inputs", [])
            outputs = tx_obj.get("out", [])
            if not isinstance(inputs, Iterable) or not isinstance(outputs, Iterable):
                continue
            for input_entry in inputs:
                if not isinstance(input_entry, Mapping):
                    continue
                prev_out = input_entry.get("prev_out")
                if isinstance(prev_out, Mapping):
                    from_addr = _safe_address(prev_out.get("addr"))
                else:
                    from_addr = _safe_address(input_entry.get("addr"))
                for output_entry in outputs:
                    if not isinstance(output_entry, Mapping):
                        continue
                    to_addr = _safe_address(output_entry.get("addr"))
                    amount_satoshi = output_entry.get("value")
                    amount_btc = _satoshi_to_btc(amount_satoshi)
                    hops.append(
                        TransactionHop(
                            tx_hash=tx_hash,
                            from_address=from_addr,
                            to_address=to_addr,
                            amount=amount_btc,
                            timestamp=timestamp,
                            metadata={"block_height": block_height},
                        )
                    )
        hops.sort(key=lambda hop: hop.timestamp, reverse=True)
        return hops[:200]


class BlockCypherExplorerClient(_BaseExplorerClient):
    """Explorer client that pulls transactions from the free BlockCypher API."""

    _BASE_ENDPOINTS: Mapping[Network, str] = {
        Network.BITCOIN: "https://api.blockcypher.com/v1/btc/main",
        Network.LITECOIN: "https://api.blockcypher.com/v1/ltc/main",
    }

    def __init__(
        self,
        network: Network,
        *,
        session: httpx.AsyncClient | None = None,
        token: str | None = None,
    ) -> None:
        super().__init__(
            network,
            session=session,
            service_id="blockcypher",
            display_name="BlockCypher API",
        )
        if network not in self._BASE_ENDPOINTS:
            raise UnsupportedNetworkError(
                f"Сеть {network.value} не поддерживается публичным API BlockCypher."
            )
        self._base_url = self._BASE_ENDPOINTS[network]
        self._token = token

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        url = f"{self._base_url}/addrs/{address}/full"
        params: dict[str, object] = {"limit": 50, "txlimit": 50}
        if self._token:
            params["token"] = self._token
        payload = await self._request_json(url, params=params)
        transactions = payload.get("txs", [])
        if not isinstance(transactions, Iterable):
            return []
        hops: list[TransactionHop] = []

        for tx in transactions:
            if not isinstance(tx, Mapping):
                continue
            tx_hash = str(tx.get("hash") or "")
            timestamp = _parse_timestamp(tx.get("confirmed") or tx.get("received"))
            inputs = tx.get("inputs", [])
            outputs = tx.get("outputs", [])
            if not isinstance(inputs, Iterable) or not isinstance(outputs, Iterable):
                continue
            for inp in inputs:
                if not isinstance(inp, Mapping):
                    continue
                from_addr = _first_address(inp)
                for out in outputs:
                    if not isinstance(out, Mapping):
                        continue
                    to_addr = _first_address(out)
                    amount_satoshi = out.get("value") or 0
                    amount_btc = _satoshi_to_btc(amount_satoshi)
                    hop = TransactionHop(
                        tx_hash=tx_hash,
                        from_address=from_addr,
                        to_address=to_addr,
                        amount=amount_btc,
                        timestamp=timestamp,
                        metadata={"block_height": tx.get("block_height")},
                    )
                    hops.append(hop)

        hops.sort(key=lambda hop: hop.timestamp, reverse=True)
        return hops[:200]


class EtherscanExplorerClient(_BaseExplorerClient):
    """Explorer integration with the Etherscan API for Ethereum-family networks."""

    _ENDPOINTS: Mapping[Network, tuple[str, int | None]] = {
        Network.ETHEREUM: ("https://api.etherscan.io/v2/api", 1),
        Network.POLYGON: ("https://api.polygonscan.com/v2/api", 137),
    }

    def __init__(
        self,
        network: Network,
        *,
        session: httpx.AsyncClient | None = None,
        api_key: str | None,
        service_id: str = "etherscan",
    ) -> None:
        if not api_key:
            env_name = "POLYGONSCAN_API_KEY" if service_id == "polygonscan" else "ETHERSCAN_API_KEY"
            raise ExplorerAPIError(
                f"Для работы с {service_id} необходимо указать API ключ ({env_name})."
            )
        if network not in self._ENDPOINTS:
            raise UnsupportedNetworkError(
                f"Сеть {network.value} не поддерживается Etherscan/Polygonscan API."
            )
        display_name = "Polygonscan API" if service_id == "polygonscan" else "Etherscan API"
        super().__init__(
            network,
            session=session,
            service_id=service_id,
            display_name=display_name,
        )
        self._base_url, self._chain_id = self._ENDPOINTS[network]
        self._api_key = api_key

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "page": 1,
            "offset": 100,
            "sort": "desc",
            "apikey": self._api_key,
        }
        if self._chain_id is not None:
            params["chainid"] = self._chain_id
        payload = await self._request_json(self._base_url, params=params)
        status = str(payload.get("status") or "0")
        message = str(payload.get("message") or "")
        result = payload.get("result", [])
        if status != "1":
            if isinstance(result, Mapping):
                result_detail = result.get("message") or result.get("result")
            else:
                result_detail = result
            detail_str = str(result_detail or "")
            if message.lower() != "no transactions found":
                if detail_str and detail_str.lower() != message.lower():
                    error_text = f"{message} ({detail_str})"
                else:
                    error_text = message or detail_str or "Unknown error"
                raise ExplorerAPIError(
                    f"Etherscan API вернул сообщение об ошибке: {error_text}"
                )
            return []
        if isinstance(result, Mapping):
            transactions = result.get("transactions") or result.get("result") or []
        else:
            transactions = result
        if not isinstance(transactions, Iterable):
            return []
        hops: list[TransactionHop] = []
        for item in transactions:
            if not isinstance(item, Mapping):
                continue
            tx_hash = str(item.get("hash") or "")
            from_addr = _safe_address(item.get("from"))
            to_addr = _safe_address(item.get("to"))
            timestamp = _coerce_timestamp(item.get("timeStamp"))
            value = item.get("value")
            amount_eth = _wei_to_eth(value)
            metadata = {
                "gas_price": item.get("gasPrice"),
                "gas_used": item.get("gasUsed"),
                "block_number": item.get("blockNumber"),
            }
            hops.append(
                TransactionHop(
                    tx_hash=tx_hash,
                    from_address=from_addr,
                    to_address=to_addr,
                    amount=amount_eth,
                    timestamp=timestamp,
                    metadata=metadata,
                )
            )
        return hops[:200]


class TronGridExplorerClient(_BaseExplorerClient):
    """Explorer integration with TronGrid for TRON network."""

    _BASE_URLS: Mapping[Network, str] = {
        Network.TRON: "https://api.trongrid.io",
    }

    def __init__(
        self,
        network: Network,
        *,
        session: httpx.AsyncClient | None = None,
        api_key: str | None,
    ) -> None:
        if not api_key:
            raise ExplorerAPIError(
                "Для работы с TronGrid необходимо указать API ключ (TRONGRID_API_KEY)."
            )
        super().__init__(
            network,
            session=session,
            service_id="trongrid",
            display_name="TronGrid API",
        )
        if network not in self._BASE_URLS:
            raise UnsupportedNetworkError(f"Сеть {network.value} не поддерживается TronGrid API.")
        self._base_url = self._BASE_URLS[network]
        self._api_key = api_key

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        url = f"{self._base_url}/v1/accounts/{address}/transactions"
        params = {
            "limit": 50,
            "order_by": "block_timestamp,desc",
            "only_to": "false",
            "only_confirmed": "true",
        }
        headers = {"TRON-PRO-API-KEY": self._api_key}
        payload = await self._request_json(url, params=params, headers=headers)
        data = payload.get("data", [])
        if not isinstance(data, Iterable):
            return []
        hops: list[TransactionHop] = []
        for tx in data:
            if not isinstance(tx, Mapping):
                continue
            tx_hash = str(tx.get("txID") or tx.get("txid") or "")
            block_timestamp = tx.get("block_timestamp")
            timestamp = _coerce_timestamp(block_timestamp, multiplier=0.001)
            raw_data = tx.get("raw_data")
            contracts: Iterable[Mapping[str, object]]
            if isinstance(raw_data, Mapping):
                contracts = raw_data.get("contract", [])  # type: ignore[assignment]
            else:
                contracts = []
            if not isinstance(contracts, Iterable):
                contracts = []
            for contract in contracts:
                if not isinstance(contract, Mapping):
                    continue
                contract_type = contract.get("type")
                if contract_type != "TransferContract":
                    continue
                parameter = contract.get("parameter", {})
                if not isinstance(parameter, Mapping):
                    continue
                value = parameter.get("value", {})
                if not isinstance(value, Mapping):
                    continue
                from_addr = _tron_address(value.get("owner_address") or value.get("ownerAddress"))
                to_addr = _tron_address(value.get("to_address") or value.get("toAddress"))
                amount = value.get("amount")
                if amount is None:
                    continue
                hops.append(
                    TransactionHop(
                        tx_hash=tx_hash,
                        from_address=from_addr,
                        to_address=to_addr,
                        amount=_sun_to_trx(amount),
                        timestamp=timestamp,
                        metadata={"contract_type": contract_type},
                    )
                )
        hops.sort(key=lambda hop: hop.timestamp, reverse=True)
        return hops[:200]


SUPPORTED_NETWORKS: tuple[Network, ...] = (
    Network.BITCOIN,
    Network.ETHEREUM,
    Network.TRON,
    Network.LITECOIN,
    Network.POLYGON,
)


def create_explorer_clients(network: Network) -> Sequence[_BaseExplorerClient]:
    """Instantiate explorer clients for the requested network."""

    if network == Network.BITCOIN:
        blockcypher_token = get_api_key("blockcypher")
        blockchain_api_code = get_api_key("blockchain_com")
        clients: list[_BaseExplorerClient] = [
            BlockchainComExplorerClient(Network.BITCOIN, api_code=blockchain_api_code)
        ]
        try:
            clients.append(
                BlockCypherExplorerClient(Network.BITCOIN, token=blockcypher_token)
            )
        except UnsupportedNetworkError:
            pass
        return clients
    if network == Network.ETHEREUM:
        etherscan_key = get_api_key("etherscan")
        return [EtherscanExplorerClient(Network.ETHEREUM, api_key=etherscan_key)]
    if network == Network.TRON:
        trongrid_key = get_api_key("trongrid")
        return [TronGridExplorerClient(Network.TRON, api_key=trongrid_key)]
    if network == Network.LITECOIN:
        blockcypher_token = get_api_key("blockcypher")
        return [BlockCypherExplorerClient(Network.LITECOIN, token=blockcypher_token)]
    if network == Network.POLYGON:
        polygonscan_key = get_api_key("polygonscan")
        return [
            EtherscanExplorerClient(
                Network.POLYGON,
                api_key=polygonscan_key,
                service_id="polygonscan",
            )
        ]
    raise UnsupportedNetworkError(
        "Выбранная сеть не поддерживается текущими публичными интеграциями."
    )


def _current_utc_timestamp() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def _parse_timestamp(value: str | None) -> int:
    if not value:
        return _current_utc_timestamp()
    try:
        return int(_dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return _current_utc_timestamp()


def _coerce_timestamp(value: object, *, multiplier: float = 1.0) -> int:
    if isinstance(value, (int, float)):
        return int(float(value) * multiplier)
    try:
        return int(float(str(value)) * multiplier)
    except (TypeError, ValueError):
        return _current_utc_timestamp()


def _first_address(data: Mapping[str, object]) -> str:
    addresses = data.get("addresses")
    if isinstance(addresses, list) and addresses:
        return str(addresses[0])
    if isinstance(addresses, str):
        return addresses
    return _safe_address(data.get("address"))


def _safe_address(value: object) -> str:
    if value is None:
        return "Неизвестно"
    text = str(value).strip()
    return text or "Неизвестно"


def _satoshi_to_btc(value: object) -> float:
    try:
        return float(value) / 100_000_000
    except (TypeError, ValueError):
        return 0.0


def _wei_to_eth(value: object) -> float:
    try:
        return float(value) / 1_000_000_000_000_000_000
    except (TypeError, ValueError):
        return 0.0


def _sun_to_trx(value: object) -> float:
    try:
        return float(value) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _tron_address(raw: object) -> str:
    value = _safe_address(raw)
    if value == "Неизвестно":
        return value
    if value.startswith("T"):
        return value
    if all(ch in "0123456789abcdefABCDEF" for ch in value) and len(value) >= 42:
        try:
            data = bytes.fromhex(value)
        except ValueError:
            return value
        return _base58check_encode(data)
    return value


def _base58check_encode(data: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
    payload = data + checksum
    num = int.from_bytes(payload, "big")
    encoded = ""
    while num > 0:
        num, rem = divmod(num, 58)
        encoded = _B58_ALPHABET[rem] + encoded
    leading_zero_bytes = len(payload) - len(payload.lstrip(b"\0"))
    return "1" * leading_zero_bytes + encoded or "1"
