import csv
import errno
import hashlib
import json
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from app import hash_manager, mover


class CrossVolumeMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.incoming.mkdir()
        self.destination_folders = {
            "documents": str(self.documents),
            "others": str(self.organized / "others"),
        }
        self.rules = {"documents": [".txt"]}
        self.hash_db_file = self.root / "reports" / "hashes.json"
        self.stats_file = self.root / "reports" / "stats.json"
        self.history_file = self.root / "reports" / "history.csv"

        with hash_manager._cache_lock:
            self.original_cache = hash_manager._cache
            self.original_cache_path = hash_manager._cache_path
            hash_manager._cache = None
            hash_manager._cache_path = None

    def tearDown(self):
        with hash_manager._cache_lock:
            hash_manager._cache = self.original_cache
            hash_manager._cache_path = self.original_cache_path
        self.temp_dir.cleanup()

    def _source(self, name="incoming.txt", content="cross-volume content"):
        source = self.incoming / name
        source.write_text(content, encoding="utf-8")
        return source

    def _move(self, source, retries=1):
        return mover.move_file_with_retries(
            source_file=source,
            destination_folders=self.destination_folders,
            extension_lookup={".txt": "documents"},
            stats_file=str(self.stats_file),
            history_file=str(self.history_file),
            hash_db_file=str(self.hash_db_file),
            archive_by_date=False,
            rules=self.rules,
            organized_root=self.organized,
            retries=retries,
            delay=0,
            category_override="documents",
        )

    @contextmanager
    def _cross_device_patch(
        self,
        *sources,
        on_publish=None,
        on_source_stage=None,
        on_cross_boundary=None,
        cross_error=None,
    ):
        primitive = "rename" if os.name == "nt" else "link"
        real_primitive = getattr(mover.os, primitive)
        real_rename = mover.os.rename
        source_paths = {Path(source) for source in sources}

        def cross_device_then_publish(source, destination, *args, **kwargs):
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                source_path in source_paths
                and destination_path.is_relative_to(self.organized)
            ):
                if on_cross_boundary is not None:
                    on_cross_boundary(source_path, destination_path)
                if cross_error is None:
                    raise OSError(errno.EXDEV, "simulated cross-device boundary")
                raise cross_error
            if on_publish is not None and source_path.name.startswith(".filepilot-"):
                on_publish(source_path, destination_path)
            if (
                on_source_stage is not None
                and destination_path.parent.name.startswith(".filepilot-remove-")
            ):
                on_source_stage(source_path, destination_path)
            return real_primitive(source, destination, *args, **kwargs)

        def observe_source_stage(source, destination, *args, **kwargs):
            destination_path = Path(destination)
            if (
                on_source_stage is not None
                and destination_path.parent.name.startswith(".filepilot-remove-")
            ):
                on_source_stage(Path(source), destination_path)
            return real_rename(source, destination, *args, **kwargs)

        with ExitStack() as stack:
            mocked_primitive = stack.enter_context(
                patch.object(
                    mover.os,
                    primitive,
                    side_effect=cross_device_then_publish,
                )
            )
            if primitive != "rename" and on_source_stage is not None:
                stack.enter_context(
                    patch.object(
                        mover.os,
                        "rename",
                        side_effect=observe_source_stage,
                    )
                )
            yield mocked_primitive

    def _hash_db(self):
        if not self.hash_db_file.exists():
            return {}
        return json.loads(self.hash_db_file.read_text(encoding="utf-8"))

    def _history(self):
        if not self.history_file.exists():
            return []
        with open(self.history_file, newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))

    def _temporary_paths(self):
        if not self.documents.exists():
            return []
        return list(self.documents.glob(".filepilot-*.tmp"))

    def test_same_volume_move_succeeds_without_cross_volume_copy(self):
        source = self._source(content="same-volume")

        with patch("app.mover._copy_to_verified_temporary") as copy_temporary:
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(result.destination.read_text(encoding="utf-8"), "same-volume")
        copy_temporary.assert_not_called()

    def test_simulated_cross_volume_move_succeeds(self):
        source = self._source(content="move across volumes")

        with self._cross_device_patch(source):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(
            result.destination.read_text(encoding="utf-8"),
            "move across volumes",
        )
        self.assertEqual(self._temporary_paths(), [])

    def test_cross_volume_move_preserves_existing_destination(self):
        source = self._source("same-name.txt", "incoming")
        destination = self.documents / source.name
        destination.parent.mkdir(parents=True)
        destination.write_text("existing", encoding="utf-8")

        with self._cross_device_patch(source):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "existing")
        self.assertEqual(
            (self.documents / "same-name(1).txt").read_text(encoding="utf-8"),
            "incoming",
        )

    def test_cross_volume_copy_failure_does_not_register_hash(self):
        source = self._source(content="not committed")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=OSError("simulated copy failure"),
            ),
        ):
            result = self._move(source, retries=1)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.exists())
        self.assertEqual(self._hash_db(), {})

    def test_cross_volume_failure_does_not_claim_moved(self):
        source = self._source()

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=OSError("simulated copy failure"),
            ),
        ):
            result = self._move(source, retries=1)

        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIsNone(result.destination)
        self.assertIn("simulated copy failure", result.error)

    def test_source_is_removed_only_after_verified_destination_publication(self):
        source = self._source(content="ordered commit")
        real_remove = mover._remove_verified_source
        removal_observed = []

        def observe_removal(source_file, destination_file, expected_hash, state):
            removal_observed.append(
                (
                    destination_file.exists(),
                    mover.calculate_file_hash(destination_file),
                    expected_hash,
                )
            )
            return real_remove(
                source_file,
                destination_file,
                expected_hash,
                state,
            )

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._remove_verified_source",
                side_effect=observe_removal,
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        expected_hash = hashlib.sha256(b"ordered commit").hexdigest()
        self.assertEqual(
            removal_observed,
            [(True, expected_hash, expected_hash)],
        )

    def test_temp_copy_read_failure_preserves_source_and_cleans_temp(self):
        source = self._source(content="survive read failure")

        def fail_read(source_handle, _destination_handle):
            source_handle.read(1)
            raise OSError("simulated read failure")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=fail_read,
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated read failure", result.error)
        self.assertEqual(source.read_text(encoding="utf-8"), "survive read failure")
        self.assertEqual(self._temporary_paths(), [])

    def test_temp_copy_write_failure_preserves_source_and_cleans_temp(self):
        source = self._source(content="survive write failure")

        def fail_write(source_handle, destination_handle):
            destination_handle.write(source_handle.read(4))
            raise OSError("simulated write failure")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=fail_write,
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated write failure", result.error)
        self.assertEqual(source.read_text(encoding="utf-8"), "survive write failure")
        self.assertEqual(self._temporary_paths(), [])

    def test_disk_full_like_flush_failure_preserves_source_and_cleans_temp(self):
        source = self._source(content="survive disk full")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.os.fsync",
                side_effect=OSError(errno.ENOSPC, "simulated disk full"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated disk full", result.error)
        self.assertEqual(source.read_text(encoding="utf-8"), "survive disk full")
        self.assertEqual(self._temporary_paths(), [])

    def test_temp_creation_failure_preserves_source(self):
        source = self._source(content="survive temp failure")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.tempfile.mkstemp",
                side_effect=PermissionError("simulated temp creation failure"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated temp creation failure", result.error)
        self.assertEqual(source.read_text(encoding="utf-8"), "survive temp failure")

    def test_cross_volume_symlink_source_fails_safely_before_temp_creation(self):
        source = self._source(content="symlink target content")

        with (
            self._cross_device_patch(source),
            patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: Path(path) == source,
            ),
            patch("app.mover.tempfile.mkstemp") as create_temp,
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("symlink sources are not supported", result.error)
        self.assertTrue(source.exists())
        create_temp.assert_not_called()

    def test_final_publication_failure_preserves_source_and_cleans_temp(self):
        source = self._source(content="survive publication failure")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._publish_temporary_no_clobber",
                side_effect=PermissionError("simulated publication failure"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated publication failure", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(self._temporary_paths(), [])
        self.assertEqual(self._hash_db(), {})

    def test_temp_cleanup_failure_preserves_primary_error_and_source(self):
        source = self._source(content="survive cleanup failure")
        real_unlink = mover.os.unlink

        def fail_temp_cleanup(path, *args, **kwargs):
            if Path(path).name.startswith(".filepilot-"):
                raise PermissionError("simulated cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=OSError("primary copy failure"),
            ),
            patch("app.mover.os.unlink", side_effect=fail_temp_cleanup),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("primary copy failure", result.error)
        self.assertNotIn("cleanup failure", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(len(self._temporary_paths()), 1)

    def test_publication_collision_retries_without_clobber(self):
        source = self._source("collision.txt", "incoming bytes")
        sentinel = b"concurrent sentinel"
        collision_created = threading.Event()

        def collide_once(_temporary, candidate):
            if not collision_created.is_set():
                candidate.write_bytes(sentinel)
                collision_created.set()

        with self._cross_device_patch(source, on_publish=collide_once):
            result = self._move(source)

        first = self.documents / "collision.txt"
        second = self.documents / "collision(1).txt"
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(result.destination, second)
        self.assertEqual(first.read_bytes(), sentinel)
        self.assertEqual(second.read_bytes(), b"incoming bytes")
        self.assertFalse(source.exists())

    def test_publication_collision_exhaustion_preserves_source_and_collisions(self):
        source = self._source("exhausted.txt", "incoming")
        collision_paths = []

        def collide_every_time(_temporary, candidate):
            candidate.write_text("external collision", encoding="utf-8")
            collision_paths.append(candidate)

        with (
            self._cross_device_patch(source, on_publish=collide_every_time),
            patch("app.mover._MAX_CROSS_VOLUME_COLLISIONS", 2),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.exists())
        self.assertEqual(len(collision_paths), 2)
        self.assertTrue(all(path.exists() for path in collision_paths))
        self.assertTrue(
            all(
                path.read_text(encoding="utf-8") == "external collision"
                for path in collision_paths
            )
        )
        self.assertEqual(self._temporary_paths(), [])
        self.assertEqual(self._hash_db(), {})

    def test_temporary_copy_verification_failure_preserves_source(self):
        source = self._source(content="verification source")
        real_calculate = mover.calculate_file_hash

        def mismatch_temporary(path):
            if Path(path).name.startswith(".filepilot-"):
                return "0" * 64
            return real_calculate(path)

        with (
            self._cross_device_patch(source),
            patch("app.mover.calculate_file_hash", side_effect=mismatch_temporary),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("Temporary copy verification failed", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(self._temporary_paths(), [])

    def test_source_mutation_during_copy_preserves_changed_source(self):
        source = self._source(content="original content")
        real_copy = mover._copy_file_data

        def copy_then_mutate(source_handle, destination_handle):
            copied_hash = real_copy(source_handle, destination_handle)
            source.write_text("changed during copy", encoding="utf-8")
            return copied_hash

        with (
            self._cross_device_patch(source),
            patch("app.mover._copy_file_data", side_effect=copy_then_mutate),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "changed during copy")
        self.assertEqual(self._temporary_paths(), [])

    def test_same_path_replacement_before_removal_is_preserved(self):
        source = self._source(content="original instance")
        replaced = threading.Event()

        def replace_source(_temporary, _candidate):
            if not replaced.is_set():
                source.unlink()
                source.write_text("replacement instance", encoding="utf-8")
                replaced.set()

        with self._cross_device_patch(source, on_publish=replace_source):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIsNotNone(result.destination)
        self.assertTrue(result.destination.exists())
        self.assertEqual(
            result.destination.read_text(encoding="utf-8"),
            "original instance",
        )
        self.assertEqual(source.read_text(encoding="utf-8"), "replacement instance")
        self.assertEqual(self._hash_db(), {})

    def test_same_content_replacement_before_copy_is_preserved(self):
        source = self._source(content="same content")
        original_backup = self.incoming / "same-content-original.txt"
        replaced = threading.Event()

        def replace_before_fallback(_source, _destination):
            if not replaced.is_set():
                os.replace(source, original_backup)
                source.write_text("same content", encoding="utf-8")
                replaced.set()

        with self._cross_device_patch(
            source,
            on_cross_boundary=replace_before_fallback,
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(source.read_text(encoding="utf-8"), "same content")
        self.assertEqual(original_backup.read_text(encoding="utf-8"), "same content")
        self.assertFalse((self.documents / source.name).exists())
        self.assertEqual(self._hash_db(), {})

    def test_same_content_replacement_between_hash_and_snapshot_is_preserved(self):
        source = self._source(content="same content")
        original_backup = self.incoming / "hash-original.txt"
        real_calculate = mover.calculate_file_hash
        replaced = False

        def hash_then_replace(path):
            nonlocal replaced
            calculated = real_calculate(path)
            if Path(path) == source and not replaced:
                os.replace(source, original_backup)
                source.write_text("same content", encoding="utf-8")
                replaced = True
            return calculated

        with (
            self._cross_device_patch(source),
            patch("app.mover.calculate_file_hash", side_effect=hash_then_replace),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(source.read_text(encoding="utf-8"), "same content")
        self.assertEqual(original_backup.read_text(encoding="utf-8"), "same content")
        self.assertFalse((self.documents / source.name).exists())
        self.assertEqual(self._hash_db(), {})

    def test_replacement_racing_source_staging_is_restored_not_deleted(self):
        source = self._source(content="original instance")
        original_backup = self.incoming / "original-backup.txt"
        replaced = threading.Event()

        def replace_at_stage(_source, _staged):
            if not replaced.is_set():
                os.replace(source, original_backup)
                source.write_text("racing replacement", encoding="utf-8")
                replaced.set()

        with self._cross_device_patch(
            source,
            on_source_stage=replace_at_stage,
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(source.read_text(encoding="utf-8"), "racing replacement")
        self.assertEqual(
            original_backup.read_text(encoding="utf-8"),
            "original instance",
        )
        self.assertTrue(result.destination.exists())
        self.assertEqual(self._hash_db(), {})

    def test_destination_change_during_source_staging_restores_source(self):
        source = self._source(content="original content")
        destination = self.documents / source.name
        real_calculate = mover.calculate_file_hash
        first_destination_hash = True

        def mutate_after_first_destination_hash(path):
            nonlocal first_destination_hash
            path = Path(path)
            calculated = real_calculate(path)
            if path == destination and first_destination_hash:
                first_destination_hash = False
                destination.write_text("external replacement", encoding="utf-8")
            return calculated

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.calculate_file_hash",
                side_effect=mutate_after_first_destination_hash,
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(source.read_text(encoding="utf-8"), "original content")
        self.assertEqual(
            destination.read_text(encoding="utf-8"),
            "external replacement",
        )
        self.assertEqual(self._hash_db(), {})

    def test_published_destination_verification_failure_preserves_source(self):
        source = self._source(content="published content")
        final_destination = self.documents / source.name
        real_calculate = mover.calculate_file_hash

        def fail_final_verification(path):
            path = Path(path)
            if path == final_destination:
                return "f" * 64
            return real_calculate(path)

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.calculate_file_hash",
                side_effect=fail_final_verification,
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(result.destination, final_destination)
        self.assertTrue(source.exists())
        self.assertTrue(final_destination.exists())
        self.assertEqual(self._hash_db(), {})

    def test_source_deletion_failure_reports_partial_publication(self):
        source = self._source(content="published but retained")
        real_unlink = mover.os.unlink

        def fail_source_unlink(path, *args, **kwargs):
            if Path(path).parent.name.startswith(".filepilot-remove-"):
                raise PermissionError("simulated source deletion failure")
            return real_unlink(path, *args, **kwargs)

        with (
            self._cross_device_patch(source),
            patch("app.mover.os.unlink", side_effect=fail_source_unlink),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(result.destination, self.documents / source.name)
        self.assertIn("could not remove source", result.error)
        self.assertTrue(source.exists())
        self.assertTrue(result.destination.exists())
        self.assertEqual(self._hash_db(), {})
        self.assertEqual(self._history()[0]["status"], "failed")

    def test_success_registers_only_rehashed_committed_destination(self):
        source = self._source(content="register committed")
        final_destination = self.documents / source.name
        real_calculate = mover.calculate_file_hash
        hashed_paths = []

        def observe_hash(path):
            hashed_paths.append(Path(path))
            return real_calculate(path)

        with (
            self._cross_device_patch(source),
            patch("app.mover.calculate_file_hash", side_effect=observe_hash),
            patch(
                "app.mover.register_file_hash",
                wraps=mover.register_file_hash,
            ) as register,
        ):
            result = self._move(source)

        expected_hash = hashlib.sha256(b"register committed").hexdigest()
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertGreaterEqual(hashed_paths.count(final_destination), 2)
        register.assert_called_once_with(
            expected_hash,
            str(final_destination),
            str(self.hash_db_file),
        )
        self.assertTrue(
            all(
                not Path(path).name.startswith(".filepilot-")
                for path in self._hash_db().values()
            )
        )

    def test_temp_name_never_enters_hash_index_or_history(self):
        source = self._source(content="metadata paths")

        with self._cross_device_patch(source):
            result = self._move(source)

        serialized_index = json.dumps(self._hash_db())
        serialized_history = json.dumps(self._history())
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertNotIn(".filepilot-", serialized_index)
        self.assertNotIn(".filepilot-", serialized_history)
        self.assertEqual(self._history()[0]["filename"], source.name)

    def test_metadata_failures_do_not_roll_back_complete_cross_volume_move(self):
        source = self._source(content="physical move survives metadata")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.append_history",
                side_effect=OSError("simulated history failure"),
            ),
            patch(
                "app.mover.update_stats",
                side_effect=OSError("simulated stats failure"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertTrue(result.destination.exists())
        self.assertIn("History update failed", result.metadata_error)
        self.assertIn("Stats update failed", result.metadata_error)

    def test_hash_registration_failure_does_not_roll_back_cross_volume_move(self):
        source = self._source(content="registration metadata failure")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover.register_file_hash",
                side_effect=OSError("simulated registration failure"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertTrue(result.destination.exists())
        self.assertIn("Hash registration failed", result.metadata_error)
        self.assertEqual(self._hash_db(), {})

    def test_unsafe_category_is_rejected_before_temp_creation(self):
        source = self._source(content="unsafe")
        destination_folders = dict(self.destination_folders)
        destination_folders["../escape"] = str(self.root / "escape")

        with patch("app.mover.tempfile.mkstemp") as create_temp:
            result = mover.move_file_with_retries(
                source_file=source,
                destination_folders=destination_folders,
                extension_lookup={".txt": "../escape"},
                stats_file=str(self.stats_file),
                history_file=str(self.history_file),
                hash_db_file=str(self.hash_db_file),
                archive_by_date=False,
                rules={"../escape": [".txt"]},
                organized_root=self.organized,
                retries=1,
                delay=0,
                category_override="../escape",
            )

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.exists())
        create_temp.assert_not_called()

    def test_cleanup_does_not_remove_replacement_at_temporary_path(self):
        self.documents.mkdir(parents=True)
        temporary_path = self.documents / ".filepilot-owned.tmp"
        temporary_path.write_text("owned", encoding="utf-8")
        owned_state = mover._file_state(temporary_path.stat(follow_symlinks=False))
        temporary = mover._TemporaryCopy(
            temporary_path,
            owned_state,
            owned_state,
        )
        temporary_path.unlink()
        temporary_path.write_text("unrelated replacement", encoding="utf-8")

        mover._cleanup_temporary_copy(temporary)

        self.assertEqual(
            temporary_path.read_text(encoding="utf-8"),
            "unrelated replacement",
        )

    def test_cleanup_failure_does_not_remove_unrelated_file(self):
        source = self._source(content="cleanup scope")
        unrelated = self.documents / ".filepilot-unrelated.tmp"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("unrelated", encoding="utf-8")

        with (
            self._cross_device_patch(source),
            patch(
                "app.mover._copy_file_data",
                side_effect=OSError("copy stopped"),
            ),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "unrelated")

    def test_concurrent_cross_volume_same_name_moves_do_not_clobber(self):
        first_dir = self.root / "source-a"
        second_dir = self.root / "source-b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "same.txt"
        second = second_dir / "same.txt"
        first.write_text("content A", encoding="utf-8")
        second.write_text("content B", encoding="utf-8")
        results = []
        errors = []

        def run(source):
            try:
                results.append(self._move(source))
            except Exception as error:
                errors.append(error)

        self.history_file.parent.mkdir(parents=True)
        self.history_file.write_text(
            "timestamp,filename,category,status,classification_method,smart_source\n",
            encoding="utf-8",
        )
        with self._cross_device_patch(first, second):
            threads = [
                threading.Thread(target=run, args=(source,))
                for source in (first, second)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.MOVED],
        )
        self.assertEqual(len({result.destination for result in results}), 2)
        self.assertCountEqual(
            [result.destination.read_text(encoding="utf-8") for result in results],
            ["content A", "content B"],
        )

    def test_concurrent_equal_content_cross_volume_moves_remain_safe(self):
        first = self._source("first.txt", "equal cross-volume content")
        second = self._source("second.txt", "equal cross-volume content")
        results = []
        errors = []

        def run(source):
            try:
                results.append(self._move(source))
            except Exception as error:
                errors.append(error)

        self.history_file.parent.mkdir(parents=True)
        self.history_file.write_text(
            "timestamp,filename,category,status,classification_method,smart_source\n",
            encoding="utf-8",
        )
        with self._cross_device_patch(first, second):
            threads = [
                threading.Thread(target=run, args=(source,))
                for source in (first, second)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.DUPLICATE],
        )
        moved = next(result for result in results if result.status is mover.MoveStatus.MOVED)
        duplicate = next(
            result for result in results if result.status is mover.MoveStatus.DUPLICATE
        )
        self.assertTrue(moved.destination.exists())
        self.assertTrue(duplicate.source.exists())
        self.assertEqual(duplicate.duplicate_of, moved.destination)

    def test_normal_duplicate_behavior_avoids_cross_volume_publication(self):
        source = self._source(content="known duplicate")
        existing = self.documents / "existing.txt"
        existing.parent.mkdir(parents=True)
        existing.write_text("known duplicate", encoding="utf-8")
        file_hash = mover.calculate_file_hash(source)
        hash_manager.register_file_hash(
            file_hash,
            str(existing),
            str(self.hash_db_file),
        )

        with self._cross_device_patch(source) as primitive:
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.DUPLICATE)
        self.assertTrue(source.exists())
        self.assertEqual(result.duplicate_of, existing)
        primitive.assert_not_called()

    def test_windows_not_same_device_code_triggers_cross_volume_fallback(self):
        source = self._source(content="Windows error fallback")
        error = OSError("simulated Windows cross-volume error")
        error.winerror = 17

        with self._cross_device_patch(source, cross_error=error):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(
            result.destination.read_text(encoding="utf-8"),
            "Windows error fallback",
        )


if __name__ == "__main__":
    unittest.main()
