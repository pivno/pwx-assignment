from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

USER_AGENT = "entsoe-assignment-lambda/0.1"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ScrapeJob:
    name: str
    url: str
    token_query_param: str | None = None


class FetchError(RuntimeError):
    """HTTP error from the ENTSO-E API; keeps the status code for logging."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    jobs = load_jobs(event or {})
    bucket = require_env("OUTPUT_BUCKET")
    prefix = os.environ.get("OUTPUT_PREFIX", "entsoe").strip("/")
    token = load_token()
    s3 = aws_client("s3")

    results = []
    for job in jobs:
        stats: dict[str, Any] = {"job": job.name}
        stage = "fetch"
        started = time.monotonic()
        try:
            body, stats["http_status"] = fetch_url(job, token)
            stats["bytes"] = len(body)
            stats["fetch_ms"] = elapsed_ms(started)

            stage, mark = "parse", time.monotonic()
            rows = response_to_rows(body)
            csv_body = rows_to_csv(rows)
            stats["rows"] = len(rows)
            stats["parse_ms"] = elapsed_ms(mark)

            stage, mark = "upload", time.monotonic()
            key = build_s3_key(prefix, job.name)
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=csv_body.encode("utf-8"),
                ContentType="text/csv; charset=utf-8",
                Metadata={
                    "source-url": job.url[:2048],
                    "row-count": str(len(rows)),
                },
            )
            stats["s3_key"] = key
            stats["upload_ms"] = elapsed_ms(mark)
        except Exception as exc:
            if isinstance(exc, FetchError):
                stats["http_status"] = exc.status
            log_event(
                "scrape_failed",
                **stats,
                stage=stage,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                total_ms=elapsed_ms(started),
            )
            raise

        stats["total_ms"] = elapsed_ms(started)
        log_event("scrape_complete", **stats)
        results.append(stats)

    return {"ok": True, "results": results}


def log_event(event: str, **fields: Any) -> None:
    """Write one JSON line to CloudWatch Logs (queryable with Logs Insights, matched by metric filters)."""
    print(json.dumps({"event": event, **fields}, default=str))


def elapsed_ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


def load_jobs(event: dict[str, Any]) -> list[ScrapeJob]:
    raw_jobs = event.get("jobs")
    if raw_jobs is None:
        raw_jobs = json.loads(require_env("SCRAPE_JOBS"))

    jobs = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            raise ValueError("Each scrape job must be an object.")
        jobs.append(
            ScrapeJob(
                name=sanitize_name(str(raw_job["name"])),
                url=str(raw_job["url"]),
                token_query_param=raw_job.get("token_query_param"),
            )
        )

    if not jobs:
        raise ValueError("At least one scrape job is required.")
    return jobs


def fetch_url(job: ScrapeJob, token: str | None) -> tuple[bytes, int]:
    url = add_token_to_url(job.url, job.token_query_param, token)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/xml, text/xml", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body: bytes = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(f"Response for job '{job.name}' exceeded {MAX_RESPONSE_BYTES} bytes.")
            return body, int(response.status)
    except urllib.error.HTTPError as exc:
        details = exc.read(1000).decode("utf-8", errors="replace")
        raise FetchError(f"HTTP {exc.code} while fetching job '{job.name}': {details}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while fetching job '{job.name}': {exc.reason}") from exc


def response_to_rows(body: bytes) -> list[dict[str, Any]]:
    """Parse an ENTSO-E API XML response into one row per time-series point."""
    root = ET.fromstring(decode_body(body))
    root_name = strip_namespace(root.tag)
    if root_name == "Acknowledgement_MarketDocument":
        reason = " ".join(
            (el.text or "").strip() for el in root.iter() if strip_namespace(el.tag) == "text"
        ) or "no reason given"
        raise RuntimeError(f"ENTSO-E returned an acknowledgement instead of data: {reason}")
    if not (root_name.endswith("MarketDocument") and find_children(root, "TimeSeries")):
        raise ValueError(f"Unsupported XML document '{root_name}': expected an ENTSO-E *MarketDocument with TimeSeries.")
    return entsoe_timeseries_to_rows(root)


def entsoe_timeseries_to_rows(root: ET.Element) -> list[dict[str, Any]]:
    """One row per Point of every TimeSeries/Period in an ENTSO-E market document.

    Each row carries the TimeSeries metadata (e.g. businessType, bidding zone, unit,
    psrType) and a UTC timestamp computed from Period start + (position - 1) * resolution.
    For curveType A03 ("variable sized block") omitted positions repeat the previous
    point, so they are filled in to give a complete, regular series.
    """
    rows: list[dict[str, Any]] = []
    for series in find_children(root, "TimeSeries"):
        series_meta: dict[str, Any] = {}
        for child in series:
            if strip_namespace(child.tag) != "Period":
                series_meta.update(flatten_xml(child))
        curve_type = series_meta.get("curveType", "A01")

        for period in find_children(series, "Period"):
            interval = next(iter(find_children(period, "timeInterval")), None)
            start = parse_entsoe_time(child_text(interval, "start")) if interval is not None else None
            end = parse_entsoe_time(child_text(interval, "end")) if interval is not None else None
            resolution = child_text(period, "resolution")
            step = parse_resolution(resolution)

            points: dict[int, dict[str, Any]] = {}
            for point in find_children(period, "Point"):
                values: dict[str, Any] = {}
                for child in point:
                    values.update(flatten_xml(child))
                position = int(values.pop("position"))
                points[position] = values
            if not points:
                continue

            positions: list[int] = sorted(points)
            if curve_type == "A03" and start and end and step:
                last_position = max(positions[-1], int((end - start) / step))
                positions = list(range(1, last_position + 1))

            carried: dict[str, Any] = {}
            for position in positions:
                carried = points.get(position, carried)
                if not carried:
                    continue
                timestamp = start + (position - 1) * step if start and step else None
                rows.append(
                    {
                        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%MZ") if timestamp else "",
                        "position": position,
                        **carried,
                        "resolution": resolution,
                        **series_meta,
                    }
                )
    return rows


def find_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if strip_namespace(child.tag) == name]


def child_text(element: ET.Element, name: str) -> str:
    match = next(iter(find_children(element, name)), None)
    return (match.text or "").strip() if match is not None else ""


def parse_entsoe_time(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_resolution(value: str) -> timedelta | None:
    """ISO 8601 durations used by ENTSO-E: PT15M, PT30M, PT60M, PT1H, P1D, P7D.

    Calendar durations (P1M, P1Y) have no fixed length and return None.
    """
    match = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", value or "")
    if not match or not any(match.groups()):
        return None
    weeks, days, hours, minutes = (int(group or 0) for group in match.groups())
    return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes)


def flatten_xml(element: ET.Element, prefix: str = "") -> dict[str, Any]:
    name = strip_namespace(element.tag)
    path = f"{prefix}.{name}" if prefix else name
    row: dict[str, Any] = {}
    if element.text and element.text.strip():
        row[path] = element.text.strip()
    for key, value in element.attrib.items():
        row[f"{path}.@{strip_namespace(key)}"] = value
    for child in element:
        row.update(flatten_xml(child, path))
    return row


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    # Union of columns in first-seen order (deterministic, and keeps timestamp first).
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else str(row[key]) for key in fieldnames})
    return output.getvalue()


def decode_body(body: bytes) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        return body.decode("utf-8-sig")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def add_token_to_url(url: str, token_query_param: str | None, token: str | None) -> str:
    if not token_query_param or not token:
        return url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append((token_query_param, token))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def load_token() -> str | None:
    token = os.environ.get("ENTSOE_SECURITY_TOKEN")
    if token:
        return token

    secret_arn = os.environ.get("ENTSOE_TOKEN_SECRET_ARN")
    if not secret_arn:
        return None

    secret = aws_client("secretsmanager").get_secret_value(SecretId=secret_arn)
    if "SecretString" in secret:
        return str(secret["SecretString"]).strip()
    return base64.b64decode(secret["SecretBinary"]).decode("utf-8").strip()


def build_s3_key(prefix: str, job_name: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y/%m/%d/%H%M%S")
    return f"{prefix}/{job_name}/{timestamp}.csv"


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    if not cleaned:
        raise ValueError("Job name cannot be empty.")
    return cleaned


def strip_namespace(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def aws_client(service_name: str) -> Any:
    import boto3

    return boto3.client(service_name)
