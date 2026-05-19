# NotebookLM Refresh Workflow

NotebookLM is a mirror for reading and synthesis only. GitHub remains canon for
source files, issues, pull requests, labels, runner state, and merge history.

## Refresh Steps

1. Start from a clean local checkout of `alanua/Skeleton` on `main`.
2. Regenerate the sourcepack locally:

   ```bash
   python3 scripts/build_notebooklm_sourcepack.py
   ```

3. Review the generated diff:

   ```bash
   git diff -- docs/NOTEBOOKLM_SOURCEPACK.md
   ```

4. Copy the full contents of `docs/NOTEBOOKLM_SOURCEPACK.md` into the manual
   Google Doc used as the NotebookLM source.
5. Refresh or re-add that Google Doc in NotebookLM.
6. Use NotebookLM for reading, summarizing, and question answering only.
7. Verify any implementation decision against GitHub canon before changing code
   or creating runner tasks.

## Boundaries

- No live Google calls are made by the generator.
- No NotebookLM API calls are made by the generator.
- No secrets, environment files, credentials, tokens, or private project data
  belong in the sourcepack, Google Doc, NotebookLM source, GitHub issues, or
  runner comments.
- If the sourcepack looks stale, regenerate from local files rather than editing
  `docs/NOTEBOOKLM_SOURCEPACK.md` by hand.
