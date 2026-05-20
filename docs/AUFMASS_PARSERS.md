# Aufmass Parser Dependencies

This document records the limited optional parser dependency route for Aufmass intake preparation.

## Approved Parser Formats

The current operator-needed parser formats are DXF, PDF, and image/scan inputs.

DXF is the primary CAD route. The operator already converts DWG files to DXF outside this repository, so this project does not install a DWG parser or DWG converter.

PDF parsing uses:

- `pdfplumber`
- `pypdf`

Image and scan helper preparation uses:

- `pillow`
- `opencv-python-headless`
- `scikit-image`

DXF parsing preparation uses:

- `ezdxf`

## Optional Extras

The parser packages are exposed only as optional dependency groups:

- `aufmass-dxf`: `ezdxf`
- `aufmass-pdf`: `pdfplumber`, `pypdf`
- `aufmass-image`: `pillow`, `opencv-python-headless`, `scikit-image`
- `aufmass-parsers`: the same DXF, PDF, and image/scan packages combined

Base dependencies remain minimal. Installing parser packages is an explicit operator choice.

## Excluded Scope

IFC parsing is intentionally out of current scope, so `ifcopenshell` is not included.

OCR is intentionally out of current scope, so `pytesseract` is not included.

PyMuPDF is excluded from the approved PDF route.

No OS packages are installed by this repository change. No DWG parser or DWG converter is installed.

No real drawings, private construction data, private quantities, or customer source files belong in the public repository. Private pilots with real DXF, PDF, scan, or image files must stay outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
