# ClauZ3 distribution counts (maintainers)

IPO disclosure **2026-155 applies to ClauZ3 only**. The complete copyright
notice and agreement are in [license.txt](../license.txt). Package metadata
uses `LicenseRef-ClauZ3` for that agreement (including its Enhancements
paragraph), together with MIT for the unchanged vendored `deal_solver` notice.
The former first-party Google Workspace MIT file is superseded by the root
agreement. Both applicable legal texts are included in wheels and sdists.

Run manually from this checkout with Python 3.11+ and an authenticated `gh`:

```sh
uv run python scripts/github_download_counts.py
```

The script uses only the Python standard library; a standalone Python 3.11+
interpreter also works. This makes only GitHub API GET requests for
`normalform-ai/clauz3`. It writes
a new timestamped JSON snapshot to `~/.local/share/clauz3-ipo/` (directory
0700, file 0600), prints its path, and never overwrites a baseline. Keep these
private files outside Git. No runtime integration or scheduled job is used.
API failures produce explicit unknowns and exit status 1; successful empty
results produce zero. Missing `gh`, timeouts and malformed data are failures.

- **Release assets:** cumulative downloads of assets visible at collection
  time, including any visible drafts/prereleases. Both
  [releases](https://docs.github.com/en/rest/releases/releases#list-releases)
  and each release's
  [assets](https://docs.github.com/en/rest/releases/assets#list-release-assets)
  are paginated. Asset IDs and counts allow later comparisons. Deleted assets,
  inaccessible releases and activity during pagination can limit coverage;
  this is not an atomic or all-time distribution total.
- **Clones:** kept separately as GitHub's
  [rolling 14-day traffic](https://docs.github.com/en/rest/metrics/traffic#get-repository-clones),
  with UTC daily buckets and unique-cloner aggregates. Access requires suitable
  repository permissions. These are clone operations, not downloads or people.
- **Unknown:** automatically generated source ZIP/tar downloads, other channels
  (including package indexes and direct copies), and annual totals. Unknown
  means unavailable, not zero.

For fiscal-year reporting, retain dated observations and state their coverage.
Compare cumulative counts by asset ID across period boundaries, accounting for
new/deleted/replaced assets and counter decreases; a missing boundary or asset
history is a gap, not a zero. Never sum cumulative snapshots or overlapping
clone windows. If combining daily clone observations manually, keep one latest
observation per UTC date, flag partial days and missing coverage, and never sum
daily/window unique counts into annual people. Report measured channels and
gaps separately; do not extrapolate a partial baseline into an annual total.
Historical missing traffic cannot be recovered by this script. Confirm fiscal
boundaries and how IPO wants unknown channels reported before an annual report.
