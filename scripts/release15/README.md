# Build15 deployment tools

These are pinned, one-time host tools for the reviewed October 2026 Modish
deployment. They are **not** a general refresh scheduler. Their exact target,
image, archive, snapshots and database identities are intentional safety guards.
Do not remove those guards to deploy another version or run a test on production.

The approved deployment is now complete. See
[the activation result](../../docs/modish2/build15-activation-result-20261023.md).
The frozen original continuation's smoke incorrectly expected `/profile` to be
an authenticated route. It stopped under maintenance after a verified import.
`build15_postimport_resume_remote.py` consumes that exact failure and commit,
checks `/users/me`, and performs only the original API create/start/open tail.
It never replays migration/import. Its durable open marker also makes the old
recovery reject restoration after opening. All executors are historical one-shot
artifacts; do not rerun them or edit their guards for another operation.

The application image is built from source commit
`a9b00e78018bfdff3ee6a8e2d458902b7e3a0971`. These host tools and the separate
production importer are mounted from reviewed files; they do not silently modify
that image. No passwords, API keys, actual feed URLs, database dumps or runtime
configuration belong in this directory.

## Preparation and approval boundary

`build15_activation_prepare_remote.py` creates a new private activation proposal
from read-only Docker metadata. `build15_proxy_maintenance_remote.py` has a
prepare-only CLI; its apply/open functions are for an approved executor.
`build15_activation_render_remote.py` renders and verifies the full runtime
proposal without changing services. The approved executor consumes that frozen
render, rather than discovering a different deployment during maintenance.

`build15_activation_final_backup_remote.py` is the first mutating phase. It
requires explicit owner approval and pinned plan/writer/proxy proofs. It closes
nginx routes, verifies the previous nginx processes are gone, stops the exact old
API and disables its restart, then makes and restores a final backup. It does not
migrate production or start another API.

`build15_activation_continue_remote.py` consumes the verified final-backup proof.
It migrates to0028, runs the separately reviewed four-source importer, checks a
private API, then recreates only the backend bound to127.0.0.1:8000. Persistent
nginx maintenance remains until schema, catalogue, authentication and exact price
checks pass. An open-intent marker is durable before the flag is removed.

`build15_prewrite_restore_remote.py` is a separately approved manual recovery
branch. It refuses any public-write/open history or unresolved import commit.
It restores into a **new** production database, verifies all31 tables, preserves
the failed database under another name, and confirms each of two non-atomic
database renames by OID. It starts the old image only on restored0025 and keeps
the old public root-filesystem policy, credentials and storage. It never deletes
a database or retries an uncertain rename. The standalone recovery container is
recorded for subsequent deployment review.

No tool here enables a timer, publishes an APK, creates a signing key, drops a
database, or authorizes itself. Static approval strings are execution guards;
they are not evidence that a human approved the operation. Private logs and
durable outcomes must be inspected after a disconnected SSH session before any
further action. A timeout is not permission to retry an import or restore.

## Rehearsal and checks

`catalog_refresh15_clone_remote.py` supplies pinned bounded-command utilities.
`catalog_refresh15_production_import_rehearsal_remote.py` creates a new isolated
PostgreSQL server/network, restores the pinned source backup, proves migration
integrity and exercises the **unchanged** production importer. Its strict
production-like database names exist only on that separate internal network.

Run the offline host guards with:

```powershell
backend/.venv/Scripts/python.exe -m unittest discover -s scripts/release15 -p 'test_*.py' -v
```

All89 host guards passed on23 October MSK, including15 forward-resume guards.
The separate import/download helpers also
passed35 tests in the pinned Linux image with no skips and no network. A real
isolated production-importer rehearsal committed70,163 input rows from four
shops, preserving existing IDs and all unaffected rows. The original copy also
passed ten-profile and real HTTP onboarding/feed QA. These results do not claim
that production has been changed or that recovery was executed on production.

Exact proof hashes, operational constraints and remaining public-release
dependencies are recorded in
[the deployment plan](../../docs/modish2/build15-production-plan-20261021.md)
and [catalogue recovery](../../docs/modish2/catalog-recovery-20261021.md).
