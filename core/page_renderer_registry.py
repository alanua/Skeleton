from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path
from typing import Any, Callable

Renderer = Callable[[dict[str, Any], Path], None]

_RENDERERS: dict[str, Renderer] = {}


def register_renderer(template_id: str, renderer: Renderer, *, replace: bool = False) -> None:
    if not template_id or not callable(renderer):
        raise ValueError("invalid_renderer_registration")
    if template_id in _RENDERERS and not replace:
        raise ValueError("renderer_already_registered")
    _RENDERERS[template_id] = renderer


def get_renderer(template_id: str) -> Renderer | None:
    return _RENDERERS.get(template_id)


def load_renderer_entrypoint(entrypoint: str) -> Renderer:
    if ":" not in entrypoint:
        raise ValueError("invalid_renderer_entrypoint")
    module_name, function_name = entrypoint.split(":", 1)
    if not module_name or not function_name:
        raise ValueError("invalid_renderer_entrypoint")
    allowed = [item.strip() for item in os.environ.get("SKELETON_PAGE_RENDERER_ALLOWLIST", "").split(",") if item.strip()]
    if not any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in allowed):
        raise ValueError("renderer_entrypoint_not_allowed")
    module = importlib.import_module(module_name)
    renderer = getattr(module, function_name, None)
    if not callable(renderer):
        raise ValueError("renderer_entrypoint_not_callable")
    return renderer


def resolve_renderer(manifest: dict[str, Any]) -> Renderer:
    entrypoint = manifest.get("renderer_entrypoint")
    if entrypoint:
        return load_renderer_entrypoint(str(entrypoint))
    renderer = get_renderer(str(manifest.get("template_id", "")))
    if renderer is None:
        raise ValueError("unknown_template_id")
    return renderer


def _static_directory_renderer(manifest: dict[str, Any], output_dir: Path) -> None:
    source = Path(str(manifest["content_ref"]))
    if not source.is_dir():
        raise ValueError("content_ref_not_directory")
    for item in source.iterdir():
        target = output_dir / item.name
        if item.is_symlink():
            raise ValueError("content_ref_symlink")
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, target)


def _single_html_renderer(manifest: dict[str, Any], output_dir: Path) -> None:
    source = Path(str(manifest["content_ref"]))
    if not source.is_file():
        raise ValueError("content_ref_not_file")
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_dir / "index.html")
    assets_dir = manifest.get("content_assets_ref")
    if assets_dir:
        assets = Path(str(assets_dir))
        if not assets.is_dir():
            raise ValueError("content_assets_ref_not_directory")
        for item in assets.iterdir():
            target = output_dir / item.name
            if item.is_symlink():
                raise ValueError("content_assets_symlink")
            if item.is_dir():
                shutil.copytree(item, target, symlinks=False)
            elif item.is_file():
                shutil.copy2(item, target)


register_renderer("static_directory_v1", _static_directory_renderer)
register_renderer("single_html_v1", _single_html_renderer)
