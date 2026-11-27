# Release Publication Steps

The rules live in `AGENTS.md` under "Versioned publication". This file is only the mechanical part:
run these steps when publishing, nothing else.

## Steps

1. Bump the version in both places, so they stay equal:
   - `__version__` in `src/thunder_forge/__init__.py`
   - `version` in `pyproject.toml`
2. Add a `CHANGELOG.md` entry for the new version: what changed and why, in the words the release
   notes will reuse.
3. Merge the change to `main`.
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
   git rev-parse origin/main                      # merged commit
   git rev-list -n 1 v<version>                   # tagged commit: must match
   git cat-file -t v<version>                     # "tag", not "commit"
   uv run python -c "import thunder_forge; print(thunder_forge.__version__)"
   grep -n '^version' pyproject.toml
   gh release view v<version>
   ```

## Transition

Nothing merged before this rule existed is retroactively tagged: `v0.1.0` stays lightweight, and the
82 commits between `v0.2.0` and the merge of the rule are not backfilled. The first published change
under the rule is the first release.
