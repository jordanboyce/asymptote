"""HTTP route package.

`main.py` on this branch still owns most routes (~111 endpoints); `master`
has since split them into this package. New routes land here rather than
growing `main.py` further — see CLAUDE.md. Existing routes migrate on their
own schedule, so a hybrid state is expected for now.

Shared request dependencies (`get_indexer`, `require_collection_access`)
live in :mod:`api.deps` so both halves can use them without importing
`main` — which would be circular.

Submodules are deliberately *not* imported here. `main` imports `api.deps`
early, and eagerly pulling the routers in from this file would drag the
whole service graph along with it — import order should stay boring.
"""
