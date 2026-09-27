-- The web GUI's own tables, beside the results tables of services/ingest (which this
-- does not change). Every statement is idempotent: applied at start-up and by
-- `ambuild-web init`.

-- Input files (building blocks, parameter files), stored once in object storage under
-- their sha256 and referenced from recipes as "sha256:<hex>"
CREATE TABLE IF NOT EXISTS blobs (
    sha256   text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    name     text NOT NULL,          -- the name it was first uploaded under
    size     bigint NOT NULL,
    uri      text NOT NULL,
    owner    text,
    created  timestamptz NOT NULL DEFAULT now()
);

-- Saved recipes: each save of a name is a new version
CREATE TABLE IF NOT EXISTS recipes (
    recipe_id  bigserial PRIMARY KEY,
    name       text NOT NULL,
    version    integer NOT NULL,
    body       jsonb NOT NULL,
    sha256     text NOT NULL,
    owner      text,
    created    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- Agents that run submissions (local/K3s now, Slurm later); tokens are stored hashed
CREATE TABLE IF NOT EXISTS agents (
    agent_id        bigserial PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    backend         text NOT NULL,
    token_sha256    text NOT NULL UNIQUE,
    revoked         boolean NOT NULL DEFAULT false,
    host            text,
    version         text,
    capabilities    jsonb,
    summary         jsonb,
    last_heartbeat  timestamptz,
    created         timestamptz NOT NULL DEFAULT now()
);

-- The queue: one row per requested run
CREATE TABLE IF NOT EXISTS submissions (
    submission_id  bigserial PRIMARY KEY,
    name           text NOT NULL,
    recipe         jsonb NOT NULL,
    recipe_sha256  text NOT NULL,
    recipe_id      bigint REFERENCES recipes ON DELETE SET NULL,
    seed           bigint,               -- overrides the recipe's; null: the recipe's
    backend        text NOT NULL,
    resources      jsonb NOT NULL DEFAULT '{}',
    state          text NOT NULL DEFAULT 'queued' CHECK (state IN
                   ('queued', 'claimed', 'submitted', 'running', 'cancelling', 'finished', 'failed', 'cancelled')),
    priority       integer NOT NULL DEFAULT 0,
    owner          text,
    agent_id       bigint REFERENCES agents ON DELETE SET NULL,
    lease_expires  timestamptz,          -- a claim not yet started returns to the queue after this
    external_id    text,                 -- the agent's handle: host:pid, Slurm job id
    run_id         uuid NOT NULL,        -- the run it records (new for each attempt)
    attempts       integer NOT NULL DEFAULT 0,
    error          text,
    created        timestamptz NOT NULL DEFAULT now(),
    claimed        timestamptz,
    started        timestamptz,
    finished       timestamptz,
    updated        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS submissions_queue_idx ON submissions (backend, priority DESC, created)
    WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS submissions_state_idx ON submissions (state);
CREATE INDEX IF NOT EXISTS submissions_run_idx ON submissions (run_id);

-- Sweeps: one recipe over many points and seeds; each run is a submission
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id   bigserial PRIMARY KEY,
    name       text NOT NULL,
    recipe     jsonb NOT NULL,         -- the base recipe
    spec       jsonb NOT NULL,         -- parameters, rows, seeds (ambuild.sweep)
    backend    text NOT NULL,
    owner      text,
    created    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS sweep_id bigint REFERENCES sweeps ON DELETE SET NULL;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS point jsonb;         -- the sweep point's values
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS sweep_index integer; -- its place in the sweep
CREATE INDEX IF NOT EXISTS submissions_sweep_idx ON submissions (sweep_id);

-- Campaigns: sweeps that aim at a goal (ambuild.campaign); a controller (or an outside
-- decision-maker) proposes points in rounds, each round queued as a sweep
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id  bigserial PRIMARY KEY,
    name         text NOT NULL,
    recipe       jsonb NOT NULL,          -- the base recipe
    spec         jsonb NOT NULL,          -- parameters, goal, budget (normalised)
    backend      text NOT NULL,
    state        text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'paused', 'stopped', 'finished')),
    message      text,                    -- why it stopped or finished
    owner        text,
    created      timestamptz NOT NULL DEFAULT now(),
    finished     timestamptz
);
CREATE TABLE IF NOT EXISTS trials (
    trial_id     bigserial PRIMARY KEY,
    campaign_id  bigint NOT NULL REFERENCES campaigns ON DELETE CASCADE,
    number       integer NOT NULL,        -- 0, 1, ... within the campaign
    round        integer NOT NULL,        -- 1, 2, ...
    params       jsonb NOT NULL,          -- the point
    proposed_by  text,                    -- tpe, gp, qmc, random, grid, or who (external)
    sweep_id     bigint REFERENCES sweeps ON DELETE SET NULL,
    created      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, number)
);
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS trial_id bigint REFERENCES trials ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS submissions_trial_idx ON submissions (trial_id);
