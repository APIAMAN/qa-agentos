"""Deterministic log parser behind the ``log.parse`` tool.

Turns a raw log bundle into a small, sanitized, structured summary that is safe to hand to a
model: error signatures with counts, exception types, top stack frames and the time window.
Same input → same output (no clock, no randomness), so signatures are stable across runs and
usable for duplicate detection.

Supported input: Python tracebacks, Java/JVM stack traces (incl. ``Caused by``), Node.js
errors, Go panics, JSON-lines logs, and plain/leveled text logs (incl. HTTP access logs).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from qa_agentos.runtime.tools.errors import ToolInputInvalidError

PARSER_VERSION = "1.0.0"
MAX_INPUT_BYTES = 2 * 1024 * 1024  # artifact input
MAX_INLINE_CHARS = 64 * 1024  # inline ``text`` input
MAX_SIGNATURES = 25
MAX_SAMPLES = 3
MAX_FRAMES = 5
MAX_LINE_CHARS = 500

# --------------------------------------------------------------------------------------------
# Secret / PII redaction
# --------------------------------------------------------------------------------------------

_REDACTIONS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{8,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{10,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bqakey_[A-Za-z0-9_-]{10,}\b"), "[REDACTED_KEY]"),
    # scheme://user:password@host  →  scheme://user:[REDACTED]@host
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"), r"\1:[REDACTED]@"),
    # key=value / "key": "value" for secret-looking keys
    (
        re.compile(
            r"""(?i)(["']?\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|"""
            r"""authorization|auth|session[_-]?id|cookie|set-cookie|private[_-]?key)\b["']?\s*[:=]\s*)"""
            r"""("[^"]*"|'[^']*'|[^\s,;&}]+)"""
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_NUMBER]"),
]


def redact_secrets(text: str) -> str:
    """Replace credentials, tokens, keys, emails and card-like numbers with placeholders."""
    return _redact(text)[0]


def _redact(text: str) -> Tuple[str, int]:
    total = 0
    for pattern, repl in _REDACTIONS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


# --------------------------------------------------------------------------------------------
# Normalization (what makes two occurrences "the same" error)
# --------------------------------------------------------------------------------------------

_NORMALIZERS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\[REDACTED[A-Z_]*\]"), "<redacted>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<ts>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalize_message(message: str) -> str:
    out = message
    for pattern, repl in _NORMALIZERS:
        out = pattern.sub(repl, out)
    return out.strip()[:MAX_LINE_CHARS]


def _normalize_frame(frame: str) -> str:
    # Line/column numbers change with unrelated edits; the function and file identify the frame.
    frame = re.sub(r"\bline \d+", "line <n>", frame)
    frame = re.sub(r":\d+(:\d+)?\)", ")", frame)
    frame = re.sub(r":\d+(:\d+)?$", "", frame)
    frame = re.sub(r"\(([^():]+\.java):\d+\)", r"(\1)", frame)
    frame = re.sub(r"\+0x[0-9a-fA-F]+", "", frame)
    return re.sub(r"\s+", " ", frame).strip()[:MAX_LINE_CHARS]


# --------------------------------------------------------------------------------------------
# Line-level recognition
# --------------------------------------------------------------------------------------------

_TS_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"), "iso"),
    (re.compile(r"\[(\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]"), "clf"),
]
_LEVEL = re.compile(
    r"\b(FATAL|CRITICAL|CRIT|SEVERE|PANIC|ERROR|ERR|WARNING|WARN|INFO|DEBUG|TRACE)\b"
    r"|\b(fatal|critical|error|warning|warn|info|debug)(?=:)"  # lowercase only as "level:" (Node/winston)
)
_LEVEL_RANK = {"fatal": 5, "critical": 5, "panic": 5, "error": 4, "warning": 3, "info": 2, "debug": 1}
_LEVEL_ALIASES = {"crit": "critical", "severe": "critical", "err": "error", "warn": "warning", "trace": "debug"}
_HTTP_5XX = re.compile(r'"\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^"]*"\s+(5\d\d)\b')

_PY_TB_START = re.compile(r"^\s*Traceback \(most recent call last\):\s*$")
_PY_FRAME = re.compile(r'^\s+File "([^"]+)", line \d+, in (\S+)')
_EXC_LINE = re.compile(r"^\s*(?:Caused by:\s*)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:Error|Exception|Exit|Interrupt|Fault|Panic)\b[\w$]*)(?::\s*(.*))?$")
_JAVA_THREAD_EXC = re.compile(r'Exception in thread "[^"]*"\s+(\S+?)(?::\s*(.*))?$')
_JAVA_FRAME = re.compile(r"^\s+at\s+([\w$.<>]+\([^)]*\))")
_NODE_FRAME = re.compile(r"^\s+at\s+(.+)$")
_GO_PANIC = re.compile(r"^panic:\s*(.*)$")
_GO_FRAME = re.compile(r"^([\w./-]+\.[\w$.*()]+)\(.*\)$")


def _parse_ts(line: str) -> Optional[str]:
    for pattern, kind in _TS_PATTERNS:
        m = pattern.search(line)
        if not m:
            continue
        raw = m.group(1)
        try:
            if kind == "clf":
                dt = datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
            else:
                dt = datetime.fromisoformat(raw.replace(",", ".").replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # naive log timestamps are treated as UTC
        return dt.astimezone(timezone.utc).isoformat()
    return None


def _level_of(line: str) -> Optional[str]:
    m = _LEVEL.search(line)
    if m:
        lvl = (m.group(1) or m.group(2)).lower()
        return _LEVEL_ALIASES.get(lvl, lvl)
    if _HTTP_5XX.search(line):
        return "error"
    return None


_PREFIX_NOISE = re.compile(
    r"^(?:\s+|UTC\b|[:|\-]+|\[[^\]]{0,60}\]|\d+#\d+:|\*\d+|[A-Za-z_][\w$]*(?:\.[\w$]+)+\s+-(?=\s))"
)


def _strip_prefix(line: str) -> str:
    """Drop timestamp, level, [thread]/[logger] tags and logger names so the message can be normalized."""
    msg = line
    for pattern, _ in _TS_PATTERNS:
        msg = pattern.sub("", msg, count=1)
    msg = re.sub(r"^\s*\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", "", msg)  # nginx error-log timestamp
    msg = _LEVEL.sub("", msg, count=1)
    while True:
        stripped = _PREFIX_NOISE.sub("", msg, count=1)
        if stripped == msg:
            break
        msg = stripped
    return msg.strip()


# --------------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------------


@dataclass
class _Sig:
    signature: str
    kind: str
    exception_type: Optional[str]
    message: str
    level: str
    frames: List[str]
    count: int = 0
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    first_line: int = 0
    samples: List[str] = field(default_factory=list)


def make_signature(exception_type: Optional[str], message: str, frames: Iterable[str]) -> str:
    basis = "|".join([exception_type or "", normalize_message(message), *list(frames)[:3]])
    return "sig_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class _Collector:
    def __init__(self) -> None:
        self.sigs: Dict[str, _Sig] = {}
        self.order: List[str] = []

    def add(
        self,
        *,
        kind: str,
        exception_type: Optional[str],
        message: str,
        level: str,
        frames: List[str],
        ts: Optional[str],
        line_no: int,
        sample: str,
    ) -> None:
        norm_frames = [_normalize_frame(f) for f in frames[:MAX_FRAMES]]
        sig_id = make_signature(exception_type, message, norm_frames)
        sig = self.sigs.get(sig_id)
        if sig is None:
            sig = _Sig(
                signature=sig_id,
                kind=kind,
                exception_type=exception_type,
                message=normalize_message(message),
                level=level,
                frames=norm_frames,
                first_line=line_no,
            )
            self.sigs[sig_id] = sig
            self.order.append(sig_id)
        sig.count += 1
        if _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(sig.level, 0):
            sig.level = level
        if ts:
            sig.first_seen = min(filter(None, [sig.first_seen, ts]))
            sig.last_seen = max(filter(None, [sig.last_seen, ts]))
        if len(sig.samples) < MAX_SAMPLES:
            clipped = sample[:MAX_LINE_CHARS]
            if clipped not in sig.samples:
                sig.samples.append(clipped)


def _json_record(line: str) -> Optional[Mapping[str, Any]]:
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def parse_log_text(text: str) -> Dict[str, Any]:
    """Parse ``text`` (already size-limited by the caller) into the ``log.parse`` output."""
    truncated = False
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        text = text.encode("utf-8")[:MAX_INPUT_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    redacted, redaction_count = _redact(text)
    lines = redacted.splitlines()

    col = _Collector()
    formats: Dict[str, int] = {}
    level_counts = {"error": 0, "warning": 0}
    timestamps: List[str] = []
    last_ts: Optional[str] = None
    last_level: Optional[str] = None
    i = 0

    def bump(fmt: str) -> None:
        formats[fmt] = formats.get(fmt, 0) + 1

    while i < len(lines):
        line = lines[i]
        ts = _parse_ts(line)
        if ts:
            timestamps.append(ts)
            last_ts = ts

        record = _json_record(line)
        if record is not None:
            bump("json")
            lvl_raw = str(record.get("level") or record.get("severity") or record.get("lvl") or "").lower()
            lvl = _LEVEL_ALIASES.get(lvl_raw, lvl_raw)
            rts = record.get("timestamp") or record.get("time") or record.get("ts") or record.get("@timestamp")
            rec_ts = _parse_ts(str(rts)) if rts else ts
            if rec_ts and rec_ts != ts:  # ts (from the raw line) was already recorded above
                timestamps.append(rec_ts)
            if lvl in ("error", "fatal", "critical", "panic"):
                level_counts["error"] += 1
                exc = record.get("exception") or record.get("error") or {}
                exc_type = None
                frames: List[str] = []
                if isinstance(exc, Mapping):
                    exc_type = exc.get("type") or exc.get("class") or exc.get("name")
                    stack = exc.get("stack") or exc.get("stacktrace") or []
                    if isinstance(stack, str):
                        stack = stack.splitlines()
                    frames = [str(f).strip().removeprefix("at ").strip() for f in stack if str(f).strip()]
                elif isinstance(exc, str):
                    exc_type = exc.split(":")[0].strip() or None
                exc_message = exc.get("message") if isinstance(exc, Mapping) else None
                message = str(exc_message or record.get("message") or record.get("msg") or "")
                col.add(
                    kind="exception" if exc_type else "error_line",
                    exception_type=str(exc_type) if exc_type else None,
                    message=message,
                    level=lvl,
                    frames=frames,
                    ts=rec_ts,
                    line_no=i + 1,
                    sample=line.strip(),
                )
            elif lvl in ("warning",):
                level_counts["warning"] += 1
            i += 1
            continue

        # Python traceback block
        if _PY_TB_START.match(line):
            bump("python")
            frames = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                fm = _PY_FRAME.match(lines[j])
                if fm:
                    frames.append(f"{fm.group(2)} ({fm.group(1)})")
                j += 1
            exc_type, message = None, ""
            if j < len(lines):
                em = _EXC_LINE.match(lines[j]) or re.match(r"^([\w.]+):\s*(.*)$", lines[j].strip())
                if em:
                    exc_type, message = em.group(1), em.group(2) or ""
                    j += 1
            frames.reverse()  # innermost (where it broke) first
            col.add(
                kind="exception",
                exception_type=exc_type,
                message=message,
                level=last_level if last_level in ("critical", "fatal") else "error",
                frames=frames,
                ts=last_ts,
                line_no=i + 1,
                sample=f"{exc_type}: {message}" if exc_type else line.strip(),
            )
            level_counts["error"] += 1
            i = max(j, i + 1)
            continue

        # Go panic
        gm = _GO_PANIC.match(line.strip())
        if gm:
            bump("go")
            frames = []
            j = i + 1
            while j < len(lines) and len(frames) < MAX_FRAMES and (j - i) < 60:
                fr = _GO_FRAME.match(lines[j].strip())
                if fr and not lines[j].strip().startswith("goroutine"):
                    frames.append(fr.group(1))
                j += 1
            col.add(
                kind="exception",
                exception_type="panic",
                message=gm.group(1),
                level="fatal",
                frames=frames,
                ts=last_ts,
                line_no=i + 1,
                sample=line.strip(),
            )
            level_counts["error"] += 1
            i = j
            continue

        # JVM / Node exception header followed by "at ..." frames
        stripped_line = _strip_prefix(line)
        jm = _JAVA_THREAD_EXC.search(line)
        em = None if jm else _EXC_LINE.match(stripped_line)
        if (jm or em) and i + 1 < len(lines) and _NODE_FRAME.match(lines[i + 1]):
            exc_type = (jm or em).group(1)  # type: ignore[union-attr]
            message = (jm or em).group(2) or ""  # type: ignore[union-attr]
            frames = []
            j = i + 1
            cause: Optional[Tuple[str, str]] = None
            while j < len(lines):
                nxt = lines[j]
                jf = _JAVA_FRAME.match(nxt)
                nf = _NODE_FRAME.match(nxt)
                if jf or nf:
                    if cause is None and len(frames) < MAX_FRAMES:
                        frames.append(jf.group(1) if jf else nf.group(1))  # type: ignore[union-attr]
                    j += 1
                    continue
                cm = re.match(r"^\s*Caused by:\s*(\S+?)(?::\s*(.*))?$", nxt)
                if cm:
                    cause = (cm.group(1), cm.group(2) or "")
                    j += 1
                    continue
                if re.match(r"^\s*\.\.\. \d+ more", nxt):
                    j += 1
                    continue
                break
            is_java = bool(jm) or any(_JAVA_FRAME.match(lines[k]) for k in range(i + 1, min(j, i + 3)))
            bump("java" if is_java else "node")
            lvl = _level_of(line) or "error"
            col.add(
                kind="exception",
                exception_type=exc_type,
                message=message,
                level=lvl if _LEVEL_RANK.get(lvl, 0) >= 4 else "error",
                frames=frames,
                ts=ts or last_ts,
                line_no=i + 1,
                sample=f"{exc_type}: {message}" + (f" (caused by {cause[0]}: {cause[1]})" if cause else ""),
            )
            level_counts["error"] += 1
            i = j
            continue

        line_level = _level_of(line)
        if line_level:
            bump("leveled" if _LEVEL.search(line) else "access")
            last_level = line_level
        if line_level in ("error", "fatal", "critical", "panic"):
            level_counts["error"] += 1
            message = stripped_line
            m5 = _HTTP_5XX.search(line)
            if m5 and not _LEVEL.search(line):
                req = re.search(r'"\s*((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+)', line)
                path = re.sub(r"\?.*$", "", req.group(1)) if req else "request"
                message = f"HTTP_{m5.group(1)} {path}"  # "_" keeps the status out of number normalization
            col.add(
                kind="error_line",
                exception_type=None,
                message=message,
                level=line_level,
                frames=[],
                ts=ts,
                line_no=i + 1,
                sample=line.strip(),
            )
        elif line_level == "warning":
            level_counts["warning"] += 1
        i += 1

    sig_list = [col.sigs[s] for s in col.order]
    ranked = sorted(
        sig_list,
        key=lambda s: (-_LEVEL_RANK.get(s.level, 0), 0 if s.kind == "exception" else 1, -s.count, s.first_line),
    )
    signatures = [
        {
            "signature": s.signature,
            "kind": s.kind,
            "exception_type": s.exception_type,
            "message": s.message,
            "level": s.level,
            "count": s.count,
            "first_seen": s.first_seen,
            "last_seen": s.last_seen,
            "top_frames": s.frames,
            "samples": s.samples,
        }
        for s in ranked[:MAX_SIGNATURES]
    ]
    if len(ranked) > MAX_SIGNATURES:
        truncated = True

    if not formats:
        detected = "empty" if not text.strip() else "plain"
    elif len(formats) == 1:
        detected = next(iter(formats))
    else:
        detected = "mixed"

    return {
        "parser_version": PARSER_VERSION,
        "format": detected,
        "total_lines": len(lines),
        "error_count": level_counts["error"],
        "warning_count": level_counts["warning"],
        "distinct_signatures": len(sig_list),
        "time_window": {"start": min(timestamps), "end": max(timestamps)} if timestamps else None,
        "primary_signature": signatures[0]["signature"] if signatures else None,
        "signatures": signatures,
        "redactions": redaction_count,
        "truncated": truncated,
    }


async def log_parse(input_data: Mapping[str, Any], context: Any = None) -> Dict[str, Any]:
    """``log.parse`` builtin: inline ``text`` only.

    Artifact input (``artifact_id``) needs storage access and is provided by the worker-injected
    implementation (``qa_agentos.modules.bugs.tools``), which then calls ``parse_log_text``.
    """
    if input_data.get("artifact_id"):
        raise ToolInputInvalidError("Artifact input is not available in this process; pass 'text' instead.")
    text = input_data.get("text")
    if not isinstance(text, str):
        raise ToolInputInvalidError("Provide 'text' (inline log content) or 'artifact_id'.")
    if len(text) > MAX_INLINE_CHARS:
        raise ToolInputInvalidError(f"Inline text exceeds {MAX_INLINE_CHARS} characters; upload it as an artifact.")
    return parse_log_text(text)
