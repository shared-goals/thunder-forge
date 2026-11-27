# Release Publication Steps

The rules live in `AGENTS.md` under "Versioned publication". This file is only the mechanical part:
run these steps when publishing, nothing else.

## Steps

1. Bump the version in both places, so they stay equal:
   - `__version__` in `src/thunder_forge/__init__.py`
   - `version` in `pyproject.toml`
2. Add a `CHANGELOG.md` entry for the new version: what changed and why, in the words the release
   notes will reuse.
3. Confirm the owner merged the change to `main`. Do not merge it yourself.
4. Tag the merged commit and push the tag:

   ```bash
   git tag -a v<version> -m "v<version>"
   git push origin v<version>
   ```

5. Publish the GitHub release from that tag, with the changelog notes:

   ```bash
   gh release create v<version> --title v<version> --notes-file <notes.md>
   ```

6. Verify the four agree before calling the change published:

   ```bash
   git fetch origin --tags
   git rev-parse HEAD                             # must equal the tagged commit
   git rev-parse origin/main                      # merged commit
   git rev-list -n 1 v<version>                   # tagged commit: must match
   git cat-file -t v<version>                     # "tag", not "commit"
   uv run python -c "import thunder_forge; print(thunder_forge.__version__)"
   grep -n '^version' pyproject.toml
   gh release view v<version>
   ```
