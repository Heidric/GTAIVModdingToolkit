# Batch transaction safety

Batch replacement uses the same stage–verify–commit model as single-track replacement.

## Preparation

The active RPF and `sounds.dat15` are treated as read-only inputs. FusionFix mode reads an existing override when present and otherwise reads the original game file. It does not create active override files merely to prepare a batch.

Every mapping is validated before staging. Empty batches, missing input files, directory-bearing target names, and duplicate target slots are rejected before any game file is changed.

## Staging and verification

The backend creates temporary RPF and `sounds.dat15` files beside their eventual targets. Every replacement is converted in a temporary workspace, packed into the staged RPF, extracted again, and compared byte-for-byte by SHA-256. Duration changes are written only to the staged `sounds.dat15`.

Cancellation is cooperative. It is checked throughout conversion and verification and once more immediately before recovery history and backups are created.

## Commit and rollback

After verification, the backend captures one paired audio-history snapshot. Direct mode then creates timestamped RPF and `sounds.dat15` backups. A failed or cancelled preparation therefore does not create misleading direct-mode backups or recovery entries.

The two staged files are committed only after every track verifies. If either final swap fails, the previous pair is restored; a first-time FusionFix override is removed instead. Temporary files and the uncommitted history snapshot are deleted on every failure path.

The Qt worker is only an adapter around the backend transaction. Backend behavior is covered by synthetic tests without requiring GTA IV files.
