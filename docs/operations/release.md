# Release Publication Steps

Mechanical part of the rule in `AGENTS.md` under "Versioned publication".

1. In the pull request, before it merges: bump `__version__` in `src/thunder_forge/__init__.py` and
   `version` in `pyproject.toml` to the same number, and add the `CHANGELOG.md` entry for it.
2. The owner merges the pull request.
3. Verify the merged tree carries that version; stop here if it does not:

   ```bash
   git fetch origin --tags
   git show origin/main:src/thunder_forge/__init__.py | grep __version__
   git show origin/main:pyproject.toml | grep '^version'
   ```

4. Tag the **merged** commit — `origin/main`, not whatever is checked out — and publish the release
   from that tag with the changelog notes:

   ```bash
   git tag -a v<version> origin/main -m "<changelog entry>"
   git push origin v<version>
   gh release create v<version> --title v<version> --notes-from-tag
   ```

5. Verify the four agree, and that the tag, the version and the merged commit are the same release:

   ```bash
   git rev-list -n 1 v<version>                                # must equal the line below
   git rev-parse origin/main
   git cat-file -t v<version>                                  # "tag", not "commit"
   git show v<version>:src/thunder_forge/__init__.py | grep __version__
   gh release view v<version>
   ```
