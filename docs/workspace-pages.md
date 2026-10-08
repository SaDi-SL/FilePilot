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
