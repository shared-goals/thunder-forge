# Release Publication Checklist

The rule lives in `AGENTS.md` ("Versioned publication"). This file is the mechanical checklist and the
version-bump table; read it when publishing, not before.

## Steps

1. Bump the version in **both** places, and keep them equal:
   - `__version__` in `src/thunder_forge/__init__.py`
   - `version` in `pyproject.toml`
2. Add a `CHANGELOG.md` entry for the new version, in the same words the release will use.
3. Merge the change to `main`.
4. Tag the **merged** commit with an annotated tag: `git tag -a v<version> -m "v<version>"`, then
   `git push origin v<version>`.
5. Publish a GitHub release from that tag, with the changelog notes.
6. Verify all four agree before saying the change is published:

   ```bash
   git fetch origin --tags
   git rev-parse origin/main                      # merged commit
   git rev-list -n 1 v<version>                   # tagged commit: must match
   git cat-file -t v<version>                     # must be "tag", not "commit"
   uv run python -c "import thunder_forge; print(thunder_forge.__version__)"
   grep -n '^version' pyproject.toml
   gh release view v<version>
   ```

## Which version

| Change | Bump |
| --- | --- |
| Fix, test, documentation, agent rule, internal implementation | PATCH |
| Compatible contract change: new model alias, new endpoint, new or changed payload field, deprecation | MINOR |
| Substantial functional slice | MINOR |
| Incompatible change before 1.0 | Needs an explicit contract decision; publish the next MINOR |
| Incompatible change after 1.0 | MAJOR |

Group related commits into one release. Each later published change gets its own version and tag.

## Published tags

`v0.1.0` predates this rule and is lightweight; every new tag is annotated. Preserve published tags:
never delete, move or re-create one, and never tag a commit that is not reachable from `origin/main`.

## When a deploy and a release disagree

A release is not a deploy. If `/status` reports a commit that differs from the tagged one, the deploy
is not finished and the reason is unknown — say that, do not assume a cause.
