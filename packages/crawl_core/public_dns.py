"""Bounded public-DNS evidence for an administrator-trusted Fake-IP proxy.

This does not dial the returned IPs or replace the local resolver. The opt-in
caller still trusts its local proxy to route the domain to its public origin.
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import httpx

from .errors import PolicyViolation

_DOH_ENDPOINT = "https://dns.google/resolve"
_WAIT_SECONDS = 6.0
_HTTP_TIMEOUT = 2.5
_MAX_RESPONSE_BYTES = 65536
_MAX_CACHE_ENTRIES = 512
_MAX_INFLIGHT = 64
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PRIVATE_SUFFIXES = (
    "localhost",
    "local",
    "internal",
    "lan",
    "home",
    "home.arpa",
    "invalid",
    "test",
    "onion",
)
_cache: dict[str, tuple[float, frozenset[str] | None, str]] = {}
_inflight: dict[str, Future[tuple[frozenset[str], float]]] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="autospider-public-dns"
)
_generation = 0


def normalize_public_dns_name(host: str) -> str:
    """Return a syntactically valid, non-local IDNA DNS name, never an IP."""
    if not isinstance(host, str) or not host or host != host.strip():
        raise PolicyViolation("public DNS verification requires a valid DNS hostname")
    if host.endswith(".."):
        raise PolicyViolation("public DNS verification requires a valid DNS hostname")
    value = host.rstrip(".").lower()
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PolicyViolation(
            "public DNS verification requires a valid IDNA hostname"
        ) from exc
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise PolicyViolation("public DNS verification cannot authorize an IP literal")
    labels = value.split(".")
    if (
        len(value) > 253
        or len(labels) < 2
        or any(not _LABEL.fullmatch(label) for label in labels)
        or not any(char.isalpha() for char in labels[-1])
    ):
        raise PolicyViolation(
            "public DNS verification requires a fully qualified DNS hostname"
        )
    if any(
        value == suffix or value.endswith("." + suffix) for suffix in _PRIVATE_SUFFIXES
    ):
        raise PolicyViolation(
            "public DNS verification cannot authorize a local or private DNS name"
        )
    return value


def clear_public_dns_cache() -> None:
    """Clear verification evidence without trusting any in-progress old lookup."""
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _inflight.clear()


def _forget_future(host: str, completed: Future) -> None:
    with _lock:
        # Keep a newly completed future until a waiter has cached its verdict;
        # otherwise another simultaneous caller can start duplicate queries.
        if _inflight.get(host) is completed and host in _cache:
            _inflight.pop(host, None)


def _save_cache(
    host: str, addresses: frozenset[str] | None, error: str, ttl: float, generation: int
) -> None:
    with _lock:
        if generation != _generation:
            return
        _cache[host] = (time.monotonic() + ttl, addresses, error)
        future = _inflight.get(host)
        if future is not None and future.done():
            _inflight.pop(host, None)
        while len(_cache) > _MAX_CACHE_ENTRIES:
            _cache.pop(next(iter(_cache)))


def assert_verified_public_dns(host: str) -> None:
    """Accept Fake-IP evidence only after both A and AAAA public-DNS checks.

    Exact names are independently verified. Results never widen a crawl's
    domain scope, and failures (including resolver unavailability) fail closed.
    """
    key = normalize_public_dns_name(host)
    now = time.monotonic()
    created = False
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            if entry[1] is not None:
                return
            raise PolicyViolation(entry[2])
        _cache.pop(key, None)
        generation = _generation
        future = _inflight.get(key)
        if future is None:
            if len(_inflight) >= _MAX_INFLIGHT:
                raise PolicyViolation(
                    f"public DNS verification capacity exceeded for {key!r}"
                )
            future = _executor.submit(_verify_uncached, key)
            _inflight[key] = future
            created = True
    if created:
        # Register outside _lock: already-completed futures invoke immediately.
        future.add_done_callback(lambda completed: _forget_future(key, completed))
    try:
        addresses, ttl = future.result(timeout=_WAIT_SECONDS)
    except (FutureTimeoutError, TimeoutError) as exc:
        error = f"public DNS verification timed out for {key!r}"
        _save_cache(key, None, error, 30.0, generation)
        raise PolicyViolation(error) from exc
    except PolicyViolation as exc:
        error = f"public DNS verification failed for {key!r}: {exc}"
        _save_cache(key, None, error, 30.0, generation)
        raise PolicyViolation(error) from exc
    except Exception as exc:
        error = f"public DNS verification unavailable for {key!r}"
        _save_cache(key, None, error, 30.0, generation)
        raise PolicyViolation(error) from exc
    _save_cache(key, addresses, "", min(ttl, 300.0), generation)


def _verify_uncached(host: str) -> tuple[frozenset[str], float]:
    deadline = time.monotonic() + _WAIT_SECONDS
    all_addresses: set[str] = set()
    all_ttls: list[float] = []
    # Constant TLS endpoint, TLS verification and no redirects. Environment
    # proxy configuration is intentionally respected for the trusted machine.
    with httpx.Client(
        timeout=_HTTP_TIMEOUT, verify=True, follow_redirects=False
    ) as client:
        for record_type in (1, 28):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PolicyViolation("resolver deadline exceeded")
            try:
                with client.stream(
                    "GET",
                    _DOH_ENDPOINT,
                    params={
                        "name": host,
                        "type": record_type,
                        "edns_client_subnet": "0.0.0.0/0",
                    },
                    headers={"accept": "application/dns-json"},
                    timeout=min(_HTTP_TIMEOUT, remaining),
                ) as response:
                    if response.status_code != 200:
                        raise PolicyViolation("resolver did not return HTTP 200")
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if time.monotonic() >= deadline:
                            raise PolicyViolation("resolver deadline exceeded")
                        if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise PolicyViolation(
                                "resolver response exceeded its size limit"
                            )
                        raw.extend(chunk)
                payload = json.loads(raw)
            except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                raise PolicyViolation(
                    "resolver transport or JSON response invalid"
                ) from exc
            addresses, ttls = _parse_answer(host, record_type, payload)
            all_addresses.update(addresses)
            all_ttls.extend(ttls)
    if not all_addresses:
        raise PolicyViolation("resolver returned no public A or AAAA address")
    return frozenset(all_addresses), min(all_ttls, default=0.0)


def _parse_answer(
    host: str, record_type: int, payload: Any
) -> tuple[set[str], list[float]]:
    if (
        not isinstance(payload, dict)
        or type(payload.get("Status")) is not int
        or payload["Status"] != 0
        or payload.get("TC") is not False
    ):
        raise PolicyViolation("resolver returned a DNS failure or truncated answer")
    question = payload.get("Question")
    if (
        not isinstance(question, list)
        or len(question) != 1
        or not isinstance(question[0], dict)
    ):
        raise PolicyViolation("resolver question is missing or malformed")
    if (
        normalize_public_dns_name(question[0].get("name")) != host
        or type(question[0].get("type")) is not int
        or question[0]["type"] != record_type
    ):
        raise PolicyViolation(
            "resolver question does not match the exact requested host and type"
        )
    answers = payload.get("Answer", [])
    if not isinstance(answers, list) or len(answers) > 128:
        raise PolicyViolation("resolver answers are malformed or excessive")
    cnames: dict[str, str] = {}
    ip_records: list[tuple[str, str]] = []
    ttls: list[float] = []
    for answer in answers:
        if not isinstance(answer, dict) or type(answer.get("type")) is not int:
            raise PolicyViolation("resolver answer record is malformed")
        answer_type = answer["type"]
        if answer_type not in {1, 5, 28}:
            # Non-address records cannot establish public-host evidence.
            continue
        owner = normalize_public_dns_name(answer.get("name"))
        ttl = answer.get("TTL")
        if type(ttl) is not int or not 0 <= ttl <= 2**32 - 1:
            raise PolicyViolation("resolver answer TTL is invalid")
        ttls.append(float(ttl))
        if answer_type == 5:
            target = normalize_public_dns_name(answer.get("data"))
            if owner in cnames and cnames[owner] != target:
                raise PolicyViolation("resolver returned conflicting CNAME targets")
            cnames[owner] = target
            continue
        try:
            address = ipaddress.ip_address(answer.get("data"))
        except (ValueError, TypeError) as exc:
            raise PolicyViolation("resolver returned a malformed IP address") from exc
        if answer_type != record_type or address.version != (
            4 if answer_type == 1 else 6
        ):
            raise PolicyViolation(
                "resolver address does not match the requested record type"
            )
        if not address.is_global:
            raise PolicyViolation("resolver returned a non-public IP address")
        ip_records.append((owner, str(address)))
    chain = {host}
    target = host
    while target in cnames:
        target = cnames[target]
        if target in chain or len(chain) >= 16:
            raise PolicyViolation("resolver CNAME chain is cyclic or excessive")
        chain.add(target)
    if any(owner not in chain for owner in cnames) or any(
        owner not in chain for owner, _ in ip_records
    ):
        raise PolicyViolation(
            "resolver answer owner is outside the requested CNAME chain"
        )
    return {address for _, address in ip_records}, ttls
