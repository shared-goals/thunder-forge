# Release Publication Steps

Mechanical part of the rule in `AGENTS.md` under "Versioned publication".

1. In the release pull request, before it merges: bump `__version__` in `src/thunder_forge/__init__.py` and
   `version` in `pyproject.toml` to the same number, add the `CHANGELOG.md` section for it, then refresh the
   lockfile — `uv lock` — and commit the updated `uv.lock` with the bump. The root package version is pinned
   in `uv.lock`, so a bump without it leaves `uv lock --check` failing and lets the next `uv run` rewrite
   the lockfile on `main`.
2. The owner merges the pull request.
3. Verify the merged tree carries that version; stop here if it does not:

   ```bash
   git fetch origin --tags
   git show origin/main:src/thunder_forge/__init__.py | grep __version__
   git show origin/main:pyproject.toml | grep '^version'
   uv lock --check
   ```

4. Tag the **merged** commit — `origin/main`, not whatever is checked out — and publish the release
   from that tag with the changelog notes:

   ```bash
   git tag -a v<version> origin/main -m "<changelog entry>"
   git push origin v<version>
   gh release create v<version> --title v<version> --notes-from-tag
   ```

5. Verify that the tag, the version and the merged commit are the same release — the tag is annotated, it
   points at `origin/main`, and the tree it names declares the version in its own name:

   ```bash
   git rev-list -n 1 v<version>                                # must equal the line below
   git rev-parse origin/main
   git cat-file -t v<version>                                  # "tag", not "commit"
   git show v<version>:pyproject.toml | grep -qx 'version = "<version>"'
   git show v<version>:src/thunder_forge/__init__.py | grep __version__
   gh release view v<version>
   ```
