# Commands

Install:

```bash
bash scripts/install_skeleton_local_ops.sh /path/to/Skeleton /private/root /install/root
```

Check memory:

```bash
/install/root/bin/skeleton-local --private-root /private/root memory health
```

Create and calculate an example:

```bash
/install/root/bin/skeleton-local --private-root /private/root aufmass example --output input.json
/install/root/bin/skeleton-local --private-root /private/root aufmass calculate --input input.json --output-dir result
```

The result directory contains JSON, CSV and an audit file. Private values remain local.
