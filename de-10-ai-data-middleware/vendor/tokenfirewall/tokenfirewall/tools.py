"""Safe local tool registry for TokenFirewall."""

from __future__ import annotations

import ast
import hashlib
import operator
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ToolDetect = Callable[[str], bool]
ToolExecute = Callable[[str], dict[str, Any]]

TOOLS_VERSION = "2"
_MAX_EXPR_LENGTH = 200
_MAX_ABS_NUMBER = 10**12
_MAX_ABS_RESULT = 10**18
_MAX_POW_EXPONENT = 12
_MAX_FILE_BYTES = 64 * 1024
_PURE_ARITHMETIC_RE = re.compile(r"^[\d\s+\-*/%().]+$")
_WORD_ARITHMETIC_RE = re.compile(
    r"^(?:[\d\s+\-*/%().]+|\b(?:plus|minus|times|multiplied\s+by|divided\s+by|over|mod|modulo|of)\b)+$",
    re.IGNORECASE,
)
_PERCENT_OF_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s+of\s+([+-]?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_NL_ARITHMETIC_RE = re.compile(
    r"^\s*(?:what\s+is|what's|calculate|compute)\s+(.+?)\s*\??\s*$",
    re.IGNORECASE,
)
_FILE_READ_RE = re.compile(r"^\s*(?:read|show|cat)\s+(?:file\s+)?(.+?)\s*$", re.IGNORECASE)


class ToolRejected(ValueError):
    """Raised when a tool detects an unsafe or unsupported request."""


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    detect_fn: ToolDetect
    execute_fn: ToolExecute


def _extract_math_expression(query: str) -> str | None:
    candidate = query.strip()
    match = _NL_ARITHMETIC_RE.match(candidate)
    if match:
        candidate = match.group(1).strip()
    if not candidate or len(candidate) > _MAX_EXPR_LENGTH:
        return None
    percent_match = _PERCENT_OF_RE.match(candidate)
    if percent_match:
        percent, base = percent_match.groups()
        return f"({percent} / 100) * ({base})"
    if re.search(r"[A-Za-z]", candidate):
        if not _WORD_ARITHMETIC_RE.fullmatch(candidate):
            return None
        replacements = (
            (r"\bmultiplied\s+by\b", "*"),
            (r"\bdivided\s+by\b", "/"),
            (r"\btimes\b", "*"),
            (r"\bover\b", "/"),
            (r"\bplus\b", "+"),
            (r"\bminus\b", "-"),
            (r"\bmodulo\b", "%"),
            (r"\bmod\b", "%"),
        )
        for pattern, replacement in replacements:
            candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)
    if not _PURE_ARITHMETIC_RE.fullmatch(candidate):
        return None
    return candidate


def _validate_math_tree(node: ast.AST) -> None:
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.USub,
    )
    for child in ast.walk(node):
        if not isinstance(child, allowed):
            raise ToolRejected("Unsafe arithmetic expression")
        if isinstance(child, ast.Constant):
            if isinstance(child.value, bool) or not isinstance(child.value, (int, float)):
                raise ToolRejected("Only numeric constants are allowed")
            if abs(child.value) > _MAX_ABS_NUMBER:
                raise ToolRejected("Numeric constant is too large")


def _safe_eval_math(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _safe_eval_math(node.body)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolRejected("Only numeric constants are allowed")
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_safe_eval_math(node.operand)
    if isinstance(node, ast.BinOp):
        left = _safe_eval_math(node.left)
        right = _safe_eval_math(node.right)
        operations: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }
        operation = operations.get(type(node.op))
        if operation is None:
            raise ToolRejected("Unsupported arithmetic operator")
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise ToolRejected("Exponent is too large")
        result = operation(left, right)
        if abs(result) > _MAX_ABS_RESULT:
            raise ToolRejected("Arithmetic result is too large")
        return result
    raise ToolRejected("Unsafe arithmetic expression")


def _parse_safe_math(query: str) -> ast.Expression | None:
    expression = _extract_math_expression(query)
    if expression is None:
        return None
    try:
        tree = ast.parse(expression, mode="eval")
        _validate_math_tree(tree)
        return tree
    except (SyntaxError, ToolRejected, ValueError):
        return None


def _format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def math_detect(query: str) -> bool:
    """Detect only arithmetic expressions that the AST whitelist accepts."""

    tree = _parse_safe_math(query)
    if tree is None:
        return False
    try:
        _safe_eval_math(tree)
    except (ArithmeticError, OverflowError, ToolRejected):
        return False
    return True


def math_execute(query: str) -> dict[str, Any]:
    """Evaluate a safe arithmetic expression without using eval."""

    tree = _parse_safe_math(query)
    if tree is None:
        raise ToolRejected("Unsafe or unsupported arithmetic expression")
    result = _safe_eval_math(tree)
    return {
        "answer": _format_number(result),
        "metadata": {"expression": _extract_math_expression(query)},
    }


def _extract_file_path(query: str) -> str | None:
    match = _FILE_READ_RE.match(query)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None
    if len(parts) != 1:
        return None
    return parts[0]


def file_read_detect(query: str) -> bool:
    """Detect simple file-read requests."""

    return _extract_file_path(query) is not None


def make_file_read_execute(allowed_dir: str | None) -> ToolExecute:
    """Create a file-read executor constrained to ``allowed_dir``."""

    def execute(query: str) -> dict[str, Any]:
        path_text = _extract_file_path(query)
        if path_text is None:
            return {"error": "No file path found"}
        if not allowed_dir:
            return {"error": "file-read tool is disabled; configure allowed_dir"}

        root = Path(allowed_dir).expanduser().resolve()
        requested = Path(path_text).expanduser()
        if not requested.is_absolute():
            requested = root / requested
        resolved = requested.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            return {"error": "Unsafe file path: outside allowed_dir"}
        if not resolved.exists() or not resolved.is_file():
            return {"error": "File not found inside allowed_dir"}
        data = resolved.read_bytes()[:_MAX_FILE_BYTES]
        text = data.decode("utf-8", errors="replace")
        return {"answer": text, "metadata": {"path": str(resolved)}}

    return execute


class ToolRegistry:
    """Small ordered registry for safe bypass tools."""

    def __init__(self, allowed_dir: str | None = None, include_defaults: bool = True) -> None:
        self.allowed_dir = allowed_dir
        self._tools: list[RegisteredTool] = []
        if include_defaults:
            self.register_tool("math", math_detect, math_execute)
            self.register_tool(
                "file-read",
                file_read_detect,
                make_file_read_execute(allowed_dir),
            )

    def register_tool(
        self,
        name: str,
        detect_fn: ToolDetect,
        execute_fn: ToolExecute,
    ) -> None:
        """Register a tool by name."""

        if not name or not name.strip():
            raise ValueError("tool name is required")
        if not callable(detect_fn) or not callable(execute_fn):
            raise TypeError("detect_fn and execute_fn must be callable")
        normalized_name = name.strip()
        self._tools = [tool for tool in self._tools if tool.name != normalized_name]
        self._tools.append(RegisteredTool(normalized_name, detect_fn, execute_fn))

    def run_tools(self, query: str) -> dict[str, Any] | None:
        """Run the first matching tool, returning its result or ``None``."""

        for tool in self._tools:
            try:
                detected = tool.detect_fn(query)
            except Exception as exc:
                return {"name": tool.name, "error": f"Tool detection failed: {exc}"}
            if not detected:
                continue
            try:
                result = tool.execute_fn(query)
            except ToolRejected as exc:
                return {"name": tool.name, "error": str(exc)}
            except Exception as exc:
                return {"name": tool.name, "error": f"Tool execution failed: {exc}"}
            return {"name": tool.name, **result}
        return None

    def version(self) -> str:
        """Return a version string reflecting registered tool names."""

        names = ",".join(tool.name for tool in self._tools)
        digest = hashlib.sha256(names.encode("utf-8")).hexdigest()[:12]
        return f"{TOOLS_VERSION}:{digest}"


_DEFAULT_REGISTRY = ToolRegistry(
    allowed_dir=os.environ.get("TOKENFIREWALL_ALLOWED_DIR"),
    include_defaults=True,
)


def register_tool(name: str, detect_fn: ToolDetect, execute_fn: ToolExecute) -> None:
    """Register a tool in the default registry."""

    _DEFAULT_REGISTRY.register_tool(name, detect_fn, execute_fn)


def run_tools(query: str, allowed_dir: str | None = None) -> dict[str, Any] | None:
    """Run tools from the default registry or a one-off allowed-dir registry."""

    configured_allowed_dir = allowed_dir or os.environ.get("TOKENFIREWALL_ALLOWED_DIR")
    if configured_allowed_dir != _DEFAULT_REGISTRY.allowed_dir:
        return ToolRegistry(allowed_dir=configured_allowed_dir).run_tools(query)
    return _DEFAULT_REGISTRY.run_tools(query)


def tools_version() -> str:
    """Return the default registry version for cache keying."""

    return _DEFAULT_REGISTRY.version()
