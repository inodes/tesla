# JOWUA recovery scripts (October 2026)

One-off scripts used to rescue the 1 TB JOWUA drive. They have paths such as /Volumes/LaCie1 written into them and are kept as a record, not for everyday use. The full write-up is in the Tesla project: claude/jowua-recovery-2026-10-21.md.

| Script | What it did |
|---|---|
| image_disk.sh | raw image of the damaged disk to LaCie1 |
| fix_disk.sh | unmount, image, repair and check a disk in one go |
| fix_image.sh | repair and check the file system inside an image (the repair lost directory entries, so it was not used for the copy) |
| refresh_image.sh | made a fresh, unrepaired image and mounted it read-only |
| sync_jowua_rescue.sh | rsync of the footage that exists only on JOWUA into jowua-rescue |
| verify_rescue.py | content comparison of the rescue copy; `--fix` re-copied wrong files |
| cleanup_jowua_rescue.sh | detached the image and deleted the raw file |

Still in Tools/ because they work on any drive by volume UUID: inventory_jowua.py (prove every file has a verified copy and write a manifest), restore_drive.py (put files back from the manifest and verify) and reformat_drive.sh (erase as exFAT, surface test).
