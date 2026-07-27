#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, time
from pathlib import Path

from core.family_document_intake import Config, Intake, Person, State, inventory, stable_file


def load_config(path: Path) -> tuple[Config, tuple[Path, ...]]:
    value = json.loads(path.read_text(encoding='utf-8'))
    people = tuple(Person(item['person_id'], tuple(item['aliases'])) for item in value['people'])
    config = Config(
        people=people,
        archive_root=Path(value['archive_root']),
        state_path=Path(value['state_path']),
        outbox_path=Path(value['projection_outbox_path']),
        memory_command=tuple(value['memory_adapter_command']),
        calendar_command=tuple(value['calendar_adapter_command']),
        settle_seconds=float(value.get('settle_seconds', 3)),
    )
    return config, tuple(Path(item) for item in value['intake_roots'])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config, roots = load_config(args.config)
    worker = Intake(config)
    observations = State(config.state_path.with_suffix('.settling.json'))
    lock_path = config.state_path.with_suffix('.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        while True:
            counts = {'discovered': 0, 'processed': 0, 'review': 0, 'blocked': 0}
            now = time.time()
            for source in inventory(roots):
                counts['discovered'] += 1
                if not stable_file(source, observations.data, now, config.settle_seconds):
                    continue
                receipt = worker.process(source)
                key = receipt['status'].lower()
                counts['processed' if key == 'done' else key] = counts.get('processed' if key == 'done' else key, 0) + 1
            observations.save()
            print(json.dumps({'schema': 'skeleton.family_document_worker.public.v1', 'status': 'DONE', 'counts': counts}, sort_keys=True))
            if args.once:
                return 0
            time.sleep(5)
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
