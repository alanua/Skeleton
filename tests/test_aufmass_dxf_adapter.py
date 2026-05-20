from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from core import aufmass_dxf_adapter
from core.aufmass_dxf_adapter import MISSING_EZDXF_MESSAGE


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_dxf_extract.schema.json"


def test_module_import_is_optional_dependency_safe() -> None:
    module = importlib.import_module("core.aufmass_dxf_adapter")

    assert module.MISSING_EZDXF_MESSAGE == MISSING_EZDXF_MESSAGE


def test_missing_ezdxf_raises_runtime_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def missing_import(name: str) -> object:
        if name == "ezdxf":
            raise ImportError("missing ezdxf")
        return importlib.import_module(name)

    monkeypatch.setattr(aufmass_dxf_adapter.importlib, "import_module", missing_import)

    with pytest.raises(RuntimeError, match=MISSING_EZDXF_MESSAGE):
        aufmass_dxf_adapter.extract_dxf(tmp_path / "missing.dxf")


def test_extract_dxf_reads_basic_geometry_annotations_and_counts(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = _write_synthetic_dxf(ezdxf, tmp_path)

    result = aufmass_dxf_adapter.extract_dxf(dxf_path)

    assert result.units == "m"
    assert result.insunits == 6
    assert result.layer_counts == {"DIMENSIONS": 1, "NOTES": 2, "ROOMS": 2, "WALLS": 1}
    assert result.entity_counts == {
        "DIMENSION": 1,
        "LINE": 1,
        "LWPOLYLINE": 1,
        "MTEXT": 1,
        "POLYLINE": 1,
        "TEXT": 1,
    }
    assert result.lines[0].start.x == pytest.approx(0.0)
    assert result.lines[0].end.y == pytest.approx(3.0)
    assert result.polylines[0].entity_type == "LWPOLYLINE"
    assert result.polylines[0].closed is True
    assert result.polylines[0].area == pytest.approx(12.0)
    assert result.polylines[1].entity_type == "POLYLINE"
    assert result.polylines[1].closed is False
    assert result.polylines[1].area is None
    assert result.texts[0].text == "Room 101"
    assert result.mtexts[0].text == "Area note"
    assert result.dimensions[0].measurement == pytest.approx(4.0)


def test_extract_closed_polylines_returns_closed_polylines_only(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = _write_synthetic_dxf(ezdxf, tmp_path)

    polylines = aufmass_dxf_adapter.extract_closed_polylines(dxf_path)

    assert len(polylines) == 1
    assert polylines[0].closed is True
    assert polylines[0].area == pytest.approx(12.0)


def test_dxf_result_to_dict_is_json_compatible(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = _write_synthetic_dxf(ezdxf, tmp_path)

    payload = aufmass_dxf_adapter.dxf_result_to_dict(aufmass_dxf_adapter.extract_dxf(dxf_path))

    assert payload["units"] == "m"
    assert payload["polylines"][0]["area"] == pytest.approx(12.0)
    assert payload["dimensions"][0]["measurement"] == pytest.approx(4.0)
    json.dumps(payload)


def test_schema_file_exists_and_contains_expected_top_level_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_dxf_extract.schema.json"
    assert schema["required"] == [
        "path",
        "units",
        "insunits",
        "layer_counts",
        "entity_counts",
        "lines",
        "polylines",
        "texts",
        "mtexts",
        "dimensions",
    ]
    assert set(schema["properties"]) >= {
        "path",
        "units",
        "insunits",
        "layer_counts",
        "entity_counts",
        "lines",
        "polylines",
        "texts",
        "mtexts",
        "dimensions",
    }


def _write_synthetic_dxf(ezdxf: object, tmp_path: Path) -> Path:
    dxf_path = tmp_path / "synthetic_aufmass.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6
    modelspace = doc.modelspace()
    modelspace.add_line((0, 0), (0, 3), dxfattribs={"layer": "WALLS"})
    modelspace.add_lwpolyline(
        [(0, 0), (4, 0), (4, 3), (0, 3)],
        close=True,
        dxfattribs={"layer": "ROOMS"},
    )
    modelspace.add_polyline2d(
        [(10, 10), (11, 10), (11, 11)],
        dxfattribs={"layer": "ROOMS"},
    )
    modelspace.add_text(
        "Room 101",
        dxfattribs={"layer": "NOTES", "height": 0.25, "rotation": 0},
    ).set_placement((1, 1))
    modelspace.add_mtext("Area note", dxfattribs={"layer": "NOTES"}).set_location((2, 2))
    modelspace.add_linear_dim(
        base=(0, -0.5),
        p1=(0, 0),
        p2=(4, 0),
        dimstyle="EZDXF",
        dxfattribs={"layer": "DIMENSIONS"},
    )
    doc.saveas(dxf_path)
    return dxf_path
