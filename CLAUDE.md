# text-to-cad-product CLAUDE.md

## Token/Cost Rules

- Do not scan the whole repo unless explicitly needed.
- Do not read large files unless necessary.
- Do not explain every command.
- Do not produce long summaries.
- Work in small increments.
- For each task, edit only the files required.
- Final response must be short: changed files, how to test, next step.
- Prefer implementing MVP v1 first only.
- Do not implement URDF/SRDF/mechatronic/G-code/Bambu yet unless explicitly asked.
- Do not run long CAD generation unless explicitly asked.
- Do not start printer/Bambu actions.

## Project Layout

- Product root: `/root/all_project_models/alfaj/text-to-cad-product`
- text-to-cad repo symlink: `repo/text-to-cad`
- CAD Python env: `/root/anaconda3/envs/cadskills/bin/python`
- CAD invocation prefix: `LD_PRELOAD=/root/anaconda3/envs/cadskills/lib/libexpat.so.1`

## MVP Roadmap

### MVP v1 (active)
Prompt → clarification → engineering brief → CAD STEP → STL/GLB → viewer → report

### MVP v2
Partitioned parts → URDF → SRDF → robot viewer

### MVP v3
Mechatronic mode → screw holes → motor mounts → battery tray → print report

### MVP v4
G-code/Bambu dry-run planning → print package
