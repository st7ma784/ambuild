-- Schema for recorded Ambuild runs. Every statement is idempotent so it can be
-- applied on each upload. Large files live in object storage; rows hold URIs.

CREATE TABLE IF NOT EXISTS runs (
    run_id          uuid PRIMARY KEY,
    parent_run_id   uuid,
    -- running, finished or failed as recorded; incomplete when the uploader
    -- finalised a run whose process ended without closing it
    status          text NOT NULL,
    started         timestamptz,
    finished        timestamptz,
    error           text,
    ambuild_version text,
    git_commit      text,
    slurm_job_id    text,
    run_json        jsonb NOT NULL,
    first_uploaded  timestamptz NOT NULL DEFAULT now(),
    last_uploaded   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs (parent_run_id);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);

-- Every line of events.jsonl, in order
CREATE TABLE IF NOT EXISTS events (
    run_id    uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
    seq       integer NOT NULL,
    type      text NOT NULL,
    step      integer,
    timestamp double precision,
    data      jsonb NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- One row per build step (the rows of ambuild.csv)
CREATE TABLE IF NOT EXISTS steps (
    run_id             uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
    step               integer NOT NULL,
    type               text NOT NULL,
    step_time          double precision,
    total_time         double precision,
    num_frags          integer,
    num_particles      integer,
    num_blocks         integer,
    density            double precision,
    num_free_endgroups integer,
    potential_energy   double precision,
    num_tries          integer,
    file_count         integer,
    fragment_types     text,
    PRIMARY KEY (run_id, step)
);

-- Every file uploaded from the run directory
CREATE TABLE IF NOT EXISTS files (
    run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
    path   text NOT NULL,   -- relative to the run directory, / separated
    kind   text,            -- artifact kind (pickle, xyz, ...) or input/<kind>
    step   integer,         -- step at which an artifact was written
    size   bigint NOT NULL,
    sha256 text NOT NULL,
    uri    text NOT NULL,
    PRIMARY KEY (run_id, path)
);

-- Parsed Poreblazer results, one row per poreblazer_N directory
CREATE TABLE IF NOT EXISTS pore_results (
    run_id                   uuid NOT NULL REFERENCES runs ON DELETE CASCADE,
    directory                text NOT NULL,
    step                     integer,
    returncode               integer,
    version                  text,
    system_volume_a3         double precision,
    system_mass_g_mol        double precision,
    system_density_g_cm3     double precision,
    helium_volume_a3         double precision,
    helium_volume_cm3_g      double precision,
    geometric_volume_a3      double precision,
    geometric_volume_cm3_g   double precision,
    surface_area_a2          double precision,
    surface_area_m2_cm3      double precision,
    surface_area_m2_g        double precision,
    pore_limiting_diameter_a double precision,
    maximum_pore_diameter_a  double precision,
    percolated_dimensions    integer,
    psd                      jsonb,
    psd_cumulative           jsonb,
    PRIMARY KEY (run_id, directory)
);
