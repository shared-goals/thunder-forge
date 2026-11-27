# Release Publication Steps

Mechanical part of the rule in `AGENTS.md` under "Versioned publication".

1. In the pull request, before it merges: bump `__version__` in `src/thunder_forge/__init__.py`
   and `version` in `pyproject.toml` to the same number, and add the `CHANGELOG.md` entry for it.
2. The owner merges the pull request.
3. Verify the merged tree carries that version; stop here if it does not:

   ```bash
   git fetch origin --tags
   git show origin/main:src/thunder_forge/__init__.py | grep __version__
   git show origin/main:pyproject.toml | grep '^version'
   git show origin/main:CHANGELOG.md | head -20
   ```

4. Tag the **merged** commit — `origin/main`, not whatever is checked out — and push the tag:

   ```bash
   git tag -a v<version> origin/main -m "v<version>"
   git push origin v<version>
   ```

5. Publish the GitHub release from that tag, with the changelog notes:

   ```bash
   gh release create v<version> --title v<version> --notes-file <notes.md>
   ```

6. Verify the four agree, and that the tag is on the merged commit:

   ```bash
   git rev-list -n 1 v<version>    # must equal the line below
   git rev-parse origin/main
   git cat-file -t v<version>      # "tag", not "commit"
   gh release view v<version>
   ```
