from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "S2_UNIVERSAL_RUNNER_LOOP_GAP_AUDIT.md"
REFERENCE_ROW = re.compile(r"^\| `(?P<path>[^`]+)` \| `(?P<symbol>[^`]+)` \|$")


def test_documented_repository_references_exist() -> None:
    references = _audit_references()
    assert references

    for relative_path, symbol in references:
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert _python_symbol_exists(path, symbol), f"{relative_path}:{symbol}"


def test_audit_covers_required_s2_concerns() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    required_phrases = (
        "Checkpoint and resume state",
        "Lease ownership and expiry",
        "Replay and idempotency protections",
        "Recovery-comment authority",
        "Issue-body approval",
        "Receipt completeness",
        "PR #1722 assumptions",
        "Smallest Ordered Implementation Slices",
    )
    for phrase in required_phrases:
        assert phrase in text


def _audit_references() -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for line in AUDIT.read_text(encoding="utf-8").splitlines():
        match = REFERENCE_ROW.match(line)
        if match is not None:
            references.append((match.group("path"), match.group("symbol")))
    return tuple(references)


def _python_symbol_exists(path: Path, symbol: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = symbol.split(".")
    if len(parts) == 1:
        return _module_symbol_exists(tree, parts[0])
    if len(parts) == 2:
        parent = _class_node(tree, parts[0])
        return parent is not None and _class_member_exists(parent, parts[1])
    raise AssertionError(f"Unsupported documented symbol shape: {symbol}")


def _module_symbol_exists(tree: ast.Module, name: str) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported_name = alias.asname or alias.name.split(".", 1)[0]
                if imported_name == name:
                    return True
    return False


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _class_member_exists(parent: ast.ClassDef, name: str) -> bool:
    for node in parent.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                return True
    return False
