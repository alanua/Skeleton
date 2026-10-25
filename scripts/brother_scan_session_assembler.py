from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_scan_session import ScanSessionAssembler
from core.family_document_state import FamilyDocumentReceiptOutbox


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-db', required=True, type=Path)
    parser.add_argument('--output-directory', required=True, type=Path)
    parser.add_argument('--event', required=True, type=Path)
    parser.add_argument('--handoff-root', type=Path)
    args = parser.parse_args(argv)
    producer = ScanSessionAssembler(FamilyDocumentReceiptOutbox(args.state_db), args.output_directory)
    session = producer.apply(json.loads(args.event.read_text()))
    if session['status'] == 'FINALIZED' and args.handoff_root:
        producer.write_handoff(session['session_id'], args.handoff_root)
    # Public-safe status only: no paths, hashes, OCR or private identifiers.
    print(json.dumps({'status': session['status'], 'reason': session.get('reason')}))
    return 2 if session['status'] == 'REVIEW' else 0


if __name__ == '__main__':
    raise SystemExit(main())