# Ambuild architecture

How a build runs today, where it is heading, and the order the changes land in.
Roadmap items referenced here are in [`../TODO.md`](../TODO.md) §5 and §6.

## Current workflow

A user script drives a single `Cell` object. Every step is a `Cell` method;
external engines are called in-process (HOOMD-Blue) or as a subprocess
(Poreblazer), and all output lands in the current working directory.

```mermaid
flowchart LR
    script["User script<br/>(standard_inputs/*.py)"] --> cell["Cell<br/>seed / grow / join / zip"]
    cell -- "cellData() → snapshot" --> hoomd["HOOMD-Blue 1 or 2<br/>optimise / runMD"]
    hoomd -- "updateCell()" --> cell
    cell -- "os.chdir + write xyz" --> pb["Poreblazer<br/>subprocess"]
    cell --> analyse["Analyse.stop()"]

    subgraph cwd["Current working directory"]
        csv["ambuild.csv<br/>(handle never closed)"]
        log["ambuild.log"]
        pkl["step_N.pkl.gz"]
        pbdir["poreblazer_N/<br/>(output never parsed)"]
    end

    analyse --> csv
    cell --> log
    cell -- "dump()" --> pkl
    pb --> pbdir
```

Limitations this causes:

- Two runs cannot share a process or directory: output names are fixed and
  `Cell.poreblazer()` changes the process working directory.
- Poreblazer results (surface area, pore volume, pore diameters, PSD) are never
  read back, so they are missing from the CSV.
- The only machine-readable record is a CSV; everything else is pickles.

## Target workflow

The engine writes everything for a run into its own directory and emits events
through `Analyse`. It never talks to the network. A separate uploader,
`ambuild-upload` (`services/ingest`), ships run directories to PostgreSQL and
S3-compatible object storage; see [Deployment](#deployment) for when it runs.

```mermaid
flowchart LR
    script["User script / API job"] --> cell["Cell"]
    cell <--> md["MD engine interface<br/>(HOOMD 2, later HOOMD 4 / OpenMM)"]
    cell --> pb["Poreblazer adapter<br/>runs in run dir, parses results"]
    cell --> rec["Analyse / Recorder<br/>emits events"]
    pb -- "pore result event" --> rec

    subgraph rundir["Run directory  runs/&lt;run-id&gt;/"]
        runjson["run.json<br/>provenance"]
        events["events.jsonl"]
        csv["ambuild.csv"]
        files["step_N.pkl.gz, *.xyz, *.cml<br/>poreblazer_N/"]
        inputs["inputs/<br/>script, params, blocks"]
    end

    rec -- "CSV sink" --> csv
    rec -- "JSONL sink" --> events
    rec --> runjson
    rec -- "input event" --> inputs
    cell -- "artifact event" --> files

    rundir -. "after the job" .-> up["ambuild-upload<br/>(Slurm upload job / K3s CronJob)"]
    up --> pg[("PostgreSQL<br/>runs · events · steps · files · pore_results")]
    up --> s3[("S3-compatible storage<br/>SeaweedFS / MinIO / Ceph / S3")]
    api["Web API"] --> pg
    api --> s3
```

Event types emitted by the recorder:

| Event | Emitted from | Payload |
| --- | --- | --- |
| `run_started` | `Cell` creation | run id |
| `input` | `Cell` creation, `libraryAddFragment` | kind, source, path in `inputs/`, size, sha256 |
| `step` | `Analyse.stop()` | the existing CSV fields |
| `artifact` | `dump()`, `write*()` | path, path relative to the run directory, kind, sha256, size |
| `pore_result` | `Cell.poreblazer()` | parsed Poreblazer metrics |
| `run_finished` | end of script / failure | status, error |

## Deployment

Builds run as Slurm jobs, which write their run directories to a filesystem
shared with the uploader. `deploy/slurm/submit_build.sh` submits a build and
chains the rest with dependencies, so uploads are triggered by the jobs
themselves:

```mermaid
flowchart LR
    submit["submit_build.sh<br/>allocates run id"] --> build["build job<br/>ambuild_build.sbatch"]
    build -- "afterany" --> up1["upload job<br/>ambuild-upload<br/>--finalise"]
    build -- "afterok<br/>(--poreblazer)" --> fanout["fan-out job<br/>counts pickles"]
    fanout --> array["Poreblazer array<br/>one child run per pickle"]
    array -- "afterany" --> up2["upload job<br/>--recursive: build + children"]

    subgraph shared["Shared filesystem  $AMBUILD_RUNS_ROOT"]
        rd["&lt;run-id&gt;/<br/>run.json, events.jsonl,<br/>inputs/, pickles"]
        child["&lt;run-id&gt;/poreblazer_runs/step_N/<br/>child runs with<br/>parent_run_id = run-id"]
    end
    build --> rd
    array --> child

    subgraph k3s["K3s (fallback)"]
        cron["CronJob<br/>ambuild-upload-scan<br/>--scan /runs<br/>--stale-after 1 day"]
    end
    shared -. "PVC (NFS)" .- cron

    up1 --> pg[("PostgreSQL")]
    up2 --> pg
    cron --> pg
    up1 --> s3[("S3-compatible storage")]
    up2 --> s3
    cron --> s3
```

- **Status.** A build that raises is recorded `failed` (the `with Cell(...)`
  block). A job that is killed, cancelled or times out leaves `running` in
  `run.json`; the `afterany` upload job's `--finalise` records it as
  `incomplete`.
- **Idempotency.** Objects are skipped when one with the same sha256 exists;
  rows are upserted on `run_id` and natural keys (`seq`, `step`, `path`,
  Poreblazer directory). Any upload can be repeated, so the Slurm jobs and the
  CronJob can overlap safely.
- **Fallback.** The CronJob uploads finished runs that have no
  `.ambuild-uploaded` marker, and runs untouched for a day as `incomplete`, which
  covers upload jobs that never ran.
- **MPI.** Ambuild drives HOOMD from one process, so a build job uses one task;
  scale-out is by job arrays. Multi-rank HOOMD needs Ambuild to write output
  from rank 0 only (TODO §3).
- **Local stack.** `deploy/docker-compose.yml` runs PostgreSQL, SeaweedFS and the
  uploader, and the `test` and `slurm-test` profiles test them end to end
  (`deploy/README.md`). A Helm chart can mirror it later.

## Delivery plan

Each increment is a separate, reviewable change that leaves the tests green
and the existing scripts working. Later increments depend only on earlier ones.

| # | Increment | Done when | TODO |
| --- | --- | --- | --- |
| 1 | **Run context.** `Cell` takes an optional output directory; `Analyse` closes its CSV; `Cell.poreblazer()` uses `run_command(directory=...)` instead of `os.chdir`. Default behaviour unchanged. | ✅ Two cells in one process write to separate directories; no `os.chdir` in the package. Logging is still process-wide. | §5 |
| 2 | **Poreblazer results.** Parse Poreblazer output into a dict and return it from `Cell.poreblazer()`. | ✅ Parser tested against a stored sample output. | §5 |
| 3 | **Event emitter.** `Analyse` sends events to a list of sinks; the CSV writer becomes the default sink. | ✅ CSV output byte-identical to before (checked against `csv.DictWriter` over the step events). | §6 |
| 4 | **JSONL sink + provenance.** `run.json` and `events.jsonl` in the run directory. | ✅ A run can be reconstructed from its directory alone (`Cell(recordRun=True)`; tested by moving the directory and restoring from it). | §6 |
| 5 | **Uploader and schema.** Idempotent ingestion into PostgreSQL + object storage. | ✅ Re-uploading a run creates no duplicate rows (`services/ingest`; Slurm end-to-end test in `deploy/slurm/test`). | §6, §3 |
| 6 | **MD engine interface**, drop HOOMD 1. | HOOMD 2 tests pass through the interface. | §5 |
| 7 | **Split `ab_cell.py`** and add non-pickle serialisation. | No public API change. | §5 |

Increments 1–4 are useful without any server; 5 adds the uploader, the
schema and the Slurm and K3s triggers, ahead of the web API in TODO §3.
