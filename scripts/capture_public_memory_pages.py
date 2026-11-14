"""Fail-closed public CLS preflight and small document-only capture.

This deliberately does not create a replay capsule: no browser profile is used,
JavaScript is not executed, rendered DOM and dependent resources are not captured.
In particular, this tool never relaxes the project's existing DNS policy to work
around Fake-IP. A blocked preflight writes diagnostics and sends no HTTP request.
"""

from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from lxml import html


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.crawl_core.policies import assert_public_host


DEFAULT_HOSTS = ("www.cls.cn", "cls.cn")
DEFAULT_URL = "https://www.cls.cn/depth?id=1118"
USER_AGENT = "AutoSpiderResearchCapture/1.0"
MAX_REQUESTS = 80
MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 60
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
SENSITIVE_KEYS = frozenset({
    "token", "access_token", "refresh_token", "id_token", "auth", "authorization",
    "auth_code", "password", "passwd", "secret", "client_secret", "api_key", "apikey",
    "session", "sessionid", "session_id", "sid", "cookie", "set-cookie", "csrf",
    "csrf_token", "jwt", "credential", "credentials", "signature", "sig", "sign",
})
SECRET_HEADERS = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization", "x-api-key", "api-key"})
_GATE_PATH = re.compile(r"/(?:login|signin|sign-in|passport|oauth|captcha|challenge|verify)(?:/|$)", re.I)
_SECRET_QUERY = re.compile(r"([?&](?:" + "|".join(re.escape(key) for key in sorted(SENSITIVE_KEYS, key=len, reverse=True)) + r")=)[^&\s\"'<>\\]+", re.I)
_SECRET_VALUE = re.compile(r"([\"'](?:" + "|".join(re.escape(key) for key in sorted(SENSITIVE_KEYS, key=len, reverse=True)) + r")[\"']\s*:\s*)[\"'][^\"']*[\"']", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_url(url: str) -> str:
    """Remove userinfo/fragments and replace values of sensitive query keys."""
    try:
        parsed = urlsplit(str(url))
        query = [(key, "[REDACTED]" if key.lower() in SENSITIVE_KEYS else value)
                 for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, urlencode(query), ""))
    except ValueError:
        return "[UNPARSEABLE_URL]"


def redact_headers(headers: dict) -> dict[str, str]:
    result = {}
    for key, value in headers.items():
        name = str(key).lower()
        result[name] = ("[REDACTED]" if name in SECRET_HEADERS
                        else redact_url(str(value)) if name in {"location", "referer", "referrer"}
                        else redact_text(str(value)))
    return result


def redact_text(value: str) -> str:
    value = re.sub(r"https?://[^\s\"'<>]+", lambda match: redact_url(match.group(0)), str(value))
    value = _SECRET_QUERY.sub(lambda match: match.group(1) + "[REDACTED]", value)
    value = _SECRET_VALUE.sub(lambda match: match.group(1) + '"[REDACTED]"', value)
    value = re.sub(r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key)(\s*:\s*)[^\r\n]+",
                   lambda match: match.group(1) + match.group(2) + "[REDACTED]", value)
    return value


class _HeadEncodingParser(HTMLParser):
    """Read real head metadata, not markup spelled inside comments or scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.head_open = True  # HTML allows an implicit head before the body.
        self.inert = []
        self.encodings = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript", "title"}:
            self.inert.append(tag)
            return
        if self.inert:
            return
        if tag not in {"html", "head", "meta", "title", "base", "link"}:
            self.head_open = False
        if tag == "meta" and self.head_open:
            values = dict(attrs)
            encoding = values.get("charset")
            if not encoding and str(values.get("http-equiv", "")).lower() == "content-type":
                match = re.search(r"(?i)\bcharset\s*=\s*([a-z0-9_.-]+)", values.get("content") or "")
                encoding = match.group(1) if match else None
            if encoding:
                self.encodings.append(encoding.strip())

    def handle_endtag(self, tag):
        if self.inert:
            if tag == self.inert[-1]:
                self.inert.pop()
            return
        if tag in {"head", "body", "html"}:
            self.head_open = False


def decode_document(data: bytes, content_type: str) -> dict:
    """Decode only an explicitly declared/BOM encoding, without replacement."""
    encoding = None
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        # UTF-32 LE shares the UTF-16 LE prefix; do not misidentify it.
        encoding = "utf-32"
    elif data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        declared = re.search(r"(?i)\bcharset\s*=\s*[\"']?([a-z0-9_.-]+)", content_type)
        if declared:
            encoding = declared.group(1)
        else:
            # Latin-1 is only a reversible byte-to-character map for inspecting
            # ASCII tag names. It is NOT used to decode or save document text.
            parser = _HeadEncodingParser()
            parser.feed(data[:4096].decode("latin-1"))
            declared_encodings = set(item.lower() for item in parser.encodings)
            if len(declared_encodings) == 1:
                encoding = next(iter(declared_encodings))
    if not encoding:
        return {"verified": False, "text": None, "encoding": None, "reason": "encoding_not_declared"}
    try:
        canonical_encoding = codecs.lookup(encoding).name
        if canonical_encoding not in {"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be",
                                      "ascii", "gb2312", "gbk", "gb18030", "cp1252", "iso8859-1"}:
            raise ValueError("unsupported declared text encoding")
        text = data.decode(encoding, errors="strict")
        return {"verified": True, "text": text, "encoding": canonical_encoding, "reason": "strict_declared_decode"}
    except (LookupError, UnicodeError, ValueError) as error:
        return {"verified": False, "text": None, "encoding": encoding,
                "reason": "declared_decode_failed:" + type(error).__name__}


def recognize_document(data: bytes, content_type: str) -> dict:
    """Recognize headerless HTML for archival only; never rewrite HTTP metadata.

    An explicit non-HTML media type is not overridden. Missing media type needs
    both strict declared/BOM decoding and a bounded HTML document signature.
    This is not permission to execute scripts or to relax URL/robots policy.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        return {"recognized": True, "basis": "declared_html_media_type", "header_missing": False}
    if media_type:
        return {"recognized": False, "basis": "explicit_non_html_media_type", "header_missing": False}
    decoded = decode_document(data, content_type)
    if not decoded["verified"]:
        return {"recognized": False, "basis": "headerless_encoding_unverified", "header_missing": True}
    prefix = decoded["text"][:1024].lstrip("\ufeff \t\r\n")
    document_start = re.match(r"(?is)^(?:<!--.{0,512}?-->\s*)*(?:<!doctype\s+html\b[^>]*>\s*)?<html\b", prefix)
    return {"recognized": bool(document_start),
            "basis": "strict_decoding_and_html_signature" if document_start else "headerless_not_an_html_document",
            "header_missing": True}


def validate_capture_url(url: str, allowed_hosts=DEFAULT_HOSTS, method: str = "GET") -> dict:
    """Pure syntactic scope check; DNS is checked separately by project policy."""
    result = {"allowed": False, "url": redact_url(url), "method": str(method).upper(), "reason": ""}
    try:
        hosts = {str(host).lower().rstrip(".") for host in allowed_hosts}
        if not hosts or any(host != "cls.cn" and not host.endswith(".cls.cn") for host in hosts):
            raise ValueError("allowed hosts must be explicit CLS hostnames")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if result["method"] not in {"GET", "HEAD"}:
            raise ValueError("only GET and HEAD are permitted")
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("only absolute HTTP(S) URLs are permitted")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are forbidden")
        if host not in hosts:
            raise ValueError("hostname is not explicitly allowed")
        if parsed.port not in {None, 443 if parsed.scheme == "https" else 80}:
            raise ValueError("nondefault ports are forbidden")
        if _GATE_PATH.search(parsed.path):
            raise ValueError("login or verification endpoint is forbidden")
        if any(key.lower() in SENSITIVE_KEYS for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise ValueError("sensitive query parameter is forbidden")
        result.update(allowed=True, reason="in_explicit_scope")
    except (ValueError, TypeError) as error:
        result["reason"] = redact_text(str(error))
    return result


def preflight_url(url: str, allowed_hosts=DEFAULT_HOSTS) -> dict:
    """Honor existing administrator DNS configuration; never alter it."""
    result = validate_capture_url(url, allowed_hosts)
    result["checked_at_utc"] = _now()
    result["public_host_verified"] = False
    if not result["allowed"]:
        result["status"] = "policy_blocked"
        return result
    try:
        assert_public_host(urlsplit(url).hostname)
        result.update(status="ready_for_robots_check", public_host_verified=True)
    except Exception as error:
        result.update(allowed=False, status="policy_blocked", reason=redact_text(type(error).__name__ + ": " + str(error)))
    return result


def evaluate_robots(status: int | None, body: str, url: str, *, user_agent: str = USER_AGENT,
                    content_type: str = "text/plain") -> dict:
    """Pure fail-closed robots result: missing/unverified is never permission."""
    result = {"verified": False, "allowed": False, "reason": "robots_unverified", "http_status": status}
    if status in {401, 403}:
        result.update(verified=True, reason="robots_access_denied")
        return result
    if status != 200:
        result["reason"] = "robots_redirect_not_followed" if status and 300 <= status < 400 else "robots_unavailable"
        return result
    if content_type.split(";", 1)[0].strip().lower() not in {"text/plain", "text/robots"}:
        result["reason"] = "robots_unexpected_content_type"
        return result
    if len(body.encode("utf-8")) > MAX_ROBOTS_BYTES or not re.search(r"(?im)^\s*user-agent\s*:", body):
        result["reason"] = "robots_not_verifiable"
        return result
    parser = RobotFileParser()
    parser.set_url(urljoin(url, "/robots.txt"))
    parser.parse(body.splitlines())
    allowed = parser.can_fetch(user_agent, url)
    result.update(verified=True, allowed=allowed, reason="robots_allowed" if allowed else "robots_disallowed")
    return result


def extract_detail_links(markup: str, list_url: str, allowed_hosts=DEFAULT_HOSTS, max_details: int = 2) -> list[str]:
    """Use only actual list anchors; never synthesize an article identifier."""
    root = html.fromstring(markup)
    selected = []
    for href in root.xpath("//a[@href]/@href"):
        target = urldefrag(urljoin(list_url, href))[0]
        if (re.fullmatch(r"/detail/\d+(?:\.html)?/?", urlsplit(target).path)
                and validate_capture_url(target, allowed_hosts)["allowed"] and target not in selected):
            selected.append(target)
        if len(selected) >= max_details:
            break
    return selected[:max_details]


def _gate(markup: str) -> str | None:
    try:
        document = html.fromstring(markup)
        title = " ".join(document.xpath("//title//text()"))
        if re.search(r"captcha|just a moment|access denied|sign in|log in|安全验证|访问验证|人机验证|请登录|用户登录|验证码", title, re.I):
            return "login_or_verification_page"
        if document.xpath('//input[translate(@type,"PASSWORD","password")="password"]'):
            return "password_form_present"
    except (ValueError, TypeError):
        return "unparseable_document"
    return None


def _write_new(path: Path, value: bytes):
    with path.open("xb") as handle:
        handle.write(value)


def capture(start_url: str, output_dir: str | Path, *, max_details: int = 0, allowed_hosts=DEFAULT_HOSTS) -> dict:
    """Capture at most one list plus two raw documents, never executable replay."""
    if type(max_details) is not int or not 0 <= max_details <= 2:
        raise ValueError("max_details must be between 0 and 2")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    report = {
        "started_at_utc": _now(), "start_url": redact_url(start_url), "status": "pending",
        "collector_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "capture_mode": "bounded_document_capture_only", "replay_ready": False,
        "rendered_html_captured": False, "javascript_executed": False,
        "allowed_hosts": sorted(allowed_hosts), "max_details": max_details,
        "limits": {"requests": MAX_REQUESTS, "bytes": MAX_BYTES, "seconds": MAX_SECONDS,
                   "page_seconds": 20, "document_bytes": MAX_DOCUMENT_BYTES},
        "network_requests": 0, "network_body_bytes": 0,
        "network_requests_scope": "robots/document HTTP requests only; DNS policy verification is not counted here",
        "preflights": [], "robots": [], "pages": [], "captured_documents": 0,
        "limitations": ["Raw HTTP documents only; rendered HTML and JavaScript/API/CSS dependencies are not captured.",
                        "Not suitable for offline intervention replay.",
                        "Stored text is redacted; original-byte hashes identify responses but do not recreate redacted bytes."],
    }
    robots_cache = {}

    def fetch(url, maximum):
        if monotonic() - started >= MAX_SECONDS or report["network_requests"] >= MAX_REQUESTS:
            return {"error": "cumulative_budget_exhausted"}, b""
        checked = preflight_url(url, allowed_hosts)
        report["preflights"].append(checked)
        if not checked["allowed"]:
            return {"error": checked["reason"], "status": "policy_blocked"}, b""
        remaining = MAX_SECONDS - (monotonic() - started)
        metadata = {"url": redact_url(url), "method": "GET", "requested_at_utc": _now()}
        body = bytearray()
        report["network_requests"] += 1
        try:
            # No redirects, proxy environment, credentials, cookies or browser profile.
            with httpx.Client(trust_env=False, follow_redirects=False,
                              headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                              timeout=httpx.Timeout(max(0.1, min(10, remaining)))) as client:
                with client.stream("GET", url) as response:
                    metadata.update(http_status=response.status_code, headers=redact_headers(dict(response.headers)),
                                    content_type=response.headers.get("content-type", ""))
                    if 300 <= response.status_code < 400:
                        metadata["error"] = "redirect_not_followed"
                        return metadata, b""
                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        metadata["error"] = "unsupported_compressed_body"
                        return metadata, b""
                    iterator = response.iter_raw(chunk_size=1024)
                    for chunk in iterator:
                        if (monotonic() - started >= MAX_SECONDS or len(body) + len(chunk) > maximum
                                or report["network_body_bytes"] + len(chunk) > MAX_BYTES):
                            metadata["error"] = "response_or_time_budget_exhausted"
                            break
                        body.extend(chunk)
                        report["network_body_bytes"] += len(chunk)
            metadata.update(original_body_sha256=sha256(body).hexdigest(), original_body_bytes=len(body))
        except Exception as error:
            metadata["error"] = redact_text(type(error).__name__ + ": " + str(error))
        return metadata, bytes(body)

    def robots_for(url):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in robots_cache:
            metadata, data = fetch(origin + "/robots.txt", MAX_ROBOTS_BYTES)
            try:
                text = data.decode("utf-8-sig", errors="strict")
            except UnicodeError:
                text = ""
                metadata["error"] = "robots_utf8_decode_failed"
            safe = redact_text(text).encode("utf-8")
            if safe:
                filename = f"robots-{len(report['robots'])}.txt"
                _write_new(destination / filename, safe)
                metadata.update(saved_file=filename, saved_sha256=sha256(safe).hexdigest(), body_redacted=safe != data)
            robots_cache[origin] = (metadata, text)
            report["robots"].append(metadata)
        metadata, text = robots_cache[origin]
        decision = evaluate_robots(metadata.get("http_status") if not metadata.get("error") else None,
                                   text, url, content_type=metadata.get("content_type", ""))
        metadata.setdefault("path_decisions", []).append({"url": redact_url(url), **decision})
        return decision

    try:
        first = preflight_url(start_url, allowed_hosts)
        report["preflights"].append(first)
        _write_new(destination / "preflight.json", (json.dumps(first, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if not first["allowed"]:
            report["status"] = "policy_blocked"
        elif not robots_for(start_url)["allowed"]:
            report["status"] = "robots_not_authorized"
        else:
            start_role = "detail" if re.fullmatch(r"/detail/\d+(?:\.html)?/?", urlsplit(start_url).path) else "list"
            pending = [(start_url, start_role)]
            while pending and monotonic() - started < MAX_SECONDS:
                url, role = pending.pop(0)
                decision = robots_for(url)
                if not decision["allowed"]:
                    report["pages"].append({"url": redact_url(url), "role": role, "status": "robots_blocked", "robots": decision})
                    continue
                metadata, data = fetch(url, MAX_DOCUMENT_BYTES)
                metadata["role"] = role
                report["pages"].append(metadata)
                if metadata.get("error") or metadata.get("http_status") != 200:
                    metadata["status"] = "not_captured"
                    if role == "list":
                        break
                    continue
                recognition = recognize_document(data, metadata.get("content_type", ""))
                metadata["document_recognition"] = recognition
                if not recognition["recognized"]:
                    metadata.update(status="not_captured", error=recognition["basis"])
                    continue
                decoded = decode_document(data, metadata.get("content_type", ""))
                metadata["encoding_check"] = {key: value for key, value in decoded.items() if key != "text"}
                if not decoded["verified"]:
                    metadata.update(status="not_captured", error=decoded["reason"])
                    continue
                text = decoded["text"]
                gate = _gate(text)
                if gate:
                    metadata.update(status="verification_or_login_gate", error=gate)
                    break
                safe_text = redact_text(text)
                safe = data if safe_text == text else safe_text.encode(decoded["encoding"], errors="strict")
                filename = f"document-{len(report['pages']) - 1}-{role}.html"
                _write_new(destination / filename, safe)
                metadata.update(status="captured_raw_document", saved_file=filename,
                                saved_sha256=sha256(safe).hexdigest(), body_redacted=safe != data,
                                encoding=decoded["encoding"], saved_encoding=decoded["encoding"],
                                rendered_html=None, replay_usable=False)
                if safe != data:
                    metadata["status"] = "captured_document_redacted"
                report["captured_documents"] += 1
                if role == "list" and max_details:
                    links = extract_detail_links(text, url, allowed_hosts, max_details)
                    metadata["observed_detail_anchor_urls"] = [redact_url(link) for link in links]
                    pending.extend((link, "detail") for link in links)
            report["status"] = "document_only_capture_finished" if report["captured_documents"] else "no_documents_captured"
    except Exception as error:
        report.update(status="capture_stopped", error=redact_text(type(error).__name__ + ": " + str(error)))
    finally:
        report.update(finished_at_utc=_now(), elapsed_seconds=monotonic() - started)
        _write_new(destination / "report.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", type=Path, required=True, help="New exclusive evidence directory; never overwrite.")
    parser.add_argument("--max-details", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--allowed-host", action="append", dest="allowed_hosts")
    arguments = parser.parse_args()
    result = capture(arguments.url, arguments.output_dir, max_details=arguments.max_details,
                     allowed_hosts=tuple(arguments.allowed_hosts) if arguments.allowed_hosts else DEFAULT_HOSTS)
    print(json.dumps({key: result[key] for key in ("status", "start_url", "network_requests", "network_body_bytes", "replay_ready")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
