## Verification levels

### Every implementation commit

- `uv run ruff check .`
- `uv run pytest`

### Docker checkpoint

Run after a completed vertical slice, infrastructure or dependency changes,
database migration milestones, and before creating a Git tag.

Docker checkpoint includes:

1. Rebuild containers.
2. Start the complete stack.
3. Verify container health and logs.
4. Apply and verify Alembic migrations.
5. Check `/health` through Angie.
6. Open Swagger.
7. Perform one real smoke test of the completed user flow.