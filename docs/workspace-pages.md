# Workspace page layout

The Settings, Rules, Folders and Activity pages share the existing charcoal, purple and orange theme.

- **Settings:** General, AI classification and About & privacy tabs share one unsaved candidate. Switching tabs preserves edits. Seconds display without trailing zeros; editor precision and the original unedited values are preserved.
- **Rules:** Category list and selected-category editor appear side by side at wider widths and stack below 840 px. Filename testing uses unsaved rules and moves no files.
- **Folders:** Watch-folder actions sit with the heading; the orange accent identifies folder routing. Checked rows control monitoring. Destination and monthly grouping remain in a separate card.
- **Activity:** Search matches filenames, categories and destinations in the loaded history, combined with the result filter. It does not search older, unloaded records; bounded-history notices remain visible. Hover over truncated timestamps or paths for their full values.

Settings, Rules and Folders keep validation, saved state, revert and save buttons in a fixed footer. Monitoring must still be stopped before saving. Configuration validation, stale revision checks, provider behavior and native file operations are unchanged.

## Windows check

1. Open each page at normal and compact window sizes. Scroll and check that save/revert controls stay visible.
2. Edit a setting, switch tabs, return and check the value. Revert to discard the trial edit.
3. Select a category, try a filename and confirm its result before saving any rule changes.
4. In Activity, search for a known filename, combine with a result filter, then clear the search.

Layout was visually reviewed offscreen at 1100 × 760 and 650 × 500 page sizes. Qt Settings, configuration-page and Activity checks cover retained behavior and the new tab/search interactions. Windows display scaling should also be checked on the target PC.

## Organizing one file

Open **My Files → Organize a file**. Select a file and choose **Preview organization**.
The preview lists its category, proposed name and destination, the matching file-type
or default rule, duplicate evidence and safety result. When a name is occupied,
the proposed alternative is provisional: execution rechecks the destination and
can choose a different unused name. It never overwrites the existing file.

**Organize file** is the explicit move action and is available only after an
executable preview. Execution still performs the service's current safety checks.
The selected source is locked while the move runs. A completed result shows the
actual name/path, **Open organized file**, **Open destination folder** and
**View operation in Activity** when an operation ID was returned. Metadata warnings
remain visible even when the physical move succeeded. Failed or unconfirmed moves
offer **Check Recovery**; this does not assert that a recovery action is available.

The Activity shortcut clears unrelated filters and selects the exact operation ID,
including after an asynchronous history refresh. If the operation is absent from
the loaded history, it says so and does not select another operation. Choose
**View operation details** to evaluate Undo. Monitoring must be stopped, Undo must
pass the existing journal/filesystem checks, and its existing confirmation is
required. Opening Activity or Recovery never performs Undo automatically.

On Windows, test with disposable files: ordinary move/open, same-name files with
different content, a verified duplicate, and Undo while monitoring is stopped.
Check compact windows and display scaling. Source previews are real moves using
the shared profile, so keep valuable documents out of trial moves.
