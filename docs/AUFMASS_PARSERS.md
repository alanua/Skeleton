# Aufmass Parser Dependencies

This document records the current approved optional parser dependency route for Aufmass intake preparation. It adds dependency choices only; it does not add parser implementation, runtime automation, private drawings, or operating system package installation.

## Optional Extras

The parser packages are exposed as optional dependency groups in `pyproject.toml` so the base install stays minimal.

- `aufmass-dxf`: `ezdxf`
- `aufmass-pdf`: `pdfplumber`, `pypdf`
- `aufmass-image`: `pillow`, `opencv-python-headless`, `scikit-image`
- `aufmass-parsers`: the combined DXF, PDF, and image/scan packages listed above

## Selected Routes

DXF is the primary CAD parser path. The operator already converts DWG sources to DXF before the Skeleton route, so this repository does not install a DWG parser or a DWG converter.

PDF parsing uses `pdfplumber` with `pypdf`. This keeps the current route focused on PDF geometry/text extraction support without adding alternate PDF engines.

Image and scan preparation uses `pillow`, `opencv-python-headless`, and `scikit-image` for public-safe image loading and analysis support.

## Explicit Exclusions

IFC is intentionally out of current scope, so no `aufmass-ifc` extra and no `ifcopenshell` dependency are added.

OCR is intentionally out of current scope, so no `aufmass-ocr` extra and no `pytesseract` dependency are added.

PyMuPDF is excluded from the current parser route.

No OS packages are installed by this dependency setup. Any system-level tooling would require a separate operator-approved task.

No real or private drawings belong in the public repository. Real DXF, PDF, scan, image, and customer construction data must stay outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
