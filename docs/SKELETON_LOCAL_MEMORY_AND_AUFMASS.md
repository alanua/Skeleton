# Local memory and Aufmass

Install with `scripts/install_skeleton_local_ops.sh` from an existing Skeleton checkout.

The local command can:

- initialize and check private memory;
- save, read, list history and remove approved facts;
- create and verify backups;
- restore a backup to a separate location;
- validate reviewed room geometry;
- calculate floor, ceiling, perimeter, wall, opening and volume quantities;
- export JSON, CSV and an audit record;
- save calculation hashes to private memory.

Private project files and values stay on the local machine. Incomplete rooms are reported and excluded instead of being treated as zero. Automatic recognition of arbitrary drawings is outside this command; it accepts reviewed manual or hybrid geometry.
