# Dockerfile policy monkey tests

Each subdirectory is a mini solution layout: `{scenario}/code/Dockerfile` plus any files
referenced by `COPY`/`ADD`. Run validation from the repo root:

```bash
.venv/bin/python tests/monkey_test/docker_violations/run_checks.py
```

Or a single scenario:

```bash
.venv/bin/python -c "
from qbittensor.validator.solution.validate_docker_image import reject_dockerfile
print(reject_dockerfile('tests/monkey_test/docker_violations/violation_volume'))
"
```

Expected results:

| Directory | Policy |
|-----------|--------|
| `allowed_relative_copy` | pass |
| `allowed_multistage_copy` | pass |
| `allowed_lowercase_dockerfile` | pass |
| `violation_volume` | reject (VOLUME) |
| `violation_expose` | reject (EXPOSE) |
| `violation_copy_from_external_image` | reject (COPY --from external) |
| `violation_copy_from_unknown_stage` | reject (COPY --from unknown stage) |
| `violation_copy_from_stage_index` | reject (COPY --from index out of range) |
| `violation_copy_absolute_path` | reject (absolute host path) |
| `violation_add_absolute_path` | reject (absolute host path) |
| `violation_copy_json_absolute` | reject (absolute host path, JSON form) |
| `violation_add_parent_traversal` | reject (`..` in source path) |
| `violation_copy_nested_parent_traversal` | reject (`..` in source path) |
