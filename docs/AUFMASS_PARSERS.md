# Aufmass Parser Dependencies

This note documents the controlled optional parser dependency groups for future Aufmass source adapters.

The current Aufmass chain remains:

1. Manual adapter
2. Calculation engine
3. Exporter

No parser implementation, CAD extraction, OCR workflow, private drawing, or real project quantity is included here.

## Optional Dependency Groups

Install only the parser group needed for the adapter being developed:

```bash
python3 -m pip install ".[aufmass-dxf]"
python3 -m pip install ".[aufmass-pdf]"
python3 -m pip install ".[aufmass-ifc]"
python3 -m pip install ".[aufmass-image]"
python3 -m pip install ".[aufmass-ocr]"
```

For a development environment that intentionally needs every selected open parser wrapper:

```bash
python3 -m pip install ".[aufmass-parsers]"
```

The base project dependencies stay minimal. Parser dependencies are optional extras only.

## Selected Parser Route

| Source target | Optional extra | Python packages | Intended stage |
| --- | --- | --- | --- |
| DXF | `aufmass-dxf` | `ezdxf` | First adapter target for CAD geometry extraction from DXF. |
| PDF | `aufmass-pdf` | `pdfplumber`, `pypdf` | Later adapter target for vector/text PDF inspection and metadata/page handling. |
| IFC | `aufmass-ifc` | `ifcopenshell` | Later adapter target for BIM model quantities and spatial elements. |
| Image/scan | `aufmass-image` | `pillow`, `opencv-python-headless`, `scikit-image` | Later adapter target for raster preprocessing and measurement support. |
| OCR wrapper | `aufmass-ocr` | `pytesseract` | Later OCR bridge to a locally installed Tesseract engine. |

## DWG Boundary

DWG support is conversion-first: convert DWG to DXF outside this public repository, then process the resulting DXF through the future DXF adapter.

This repository does not add a direct DWG parser dependency. This task does not install GNU LibreDWG, ODA File Converter, or any operating-system-level DWG converter.

## PDF Licensing Boundary

PyMuPDF is deliberately excluded from this dependency set. Its AGPL/commercial licensing path needs a separate review before any future use.

## OCR System Package Caveat

`pytesseract` is only a Python wrapper. It does not include the Tesseract OCR engine.

This task does not install Tesseract or any OCR language packages. Any future OCR adapter must document the required private/local operating-system setup separately.

## Private Drawing Boundary

Real drawings, customer data, photos, contracts, invoices, and measured project quantities must stay out of the public repository.

Private pilots may use real DXF, DWG converted to DXF, PDF, IFC, scan, or image inputs only through approved private routes. Public tests and docs must use synthetic or sanitized fixtures.
