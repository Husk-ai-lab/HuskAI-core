-- husk-core (Neon, Frankfurt) — schema exactly as in "Technical plan for Husk AI" (Oct 2026).
-- Applied by CI migrations as the owner role, never by the app. Kept here so dev/staging
-- branches can be created from this repo. Husk itself has NO database credential: it reads
-- two public views through the Neon Data API as the `anonymous` role (bottom of this file).

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS billing;
CREATE SCHEMA IF NOT EXISTS usage;
CREATE SCHEMA IF NOT EXISTS runtime;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS core.organizations (
    id text PRIMARY KEY,                 -- ZITADEL org
    stripe_customer_id text UNIQUE,
    name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS core.users (
    id text PRIMARY KEY,                 -- ZITADEL user; ids only, no email or name
    org_id text NOT NULL REFERENCES core.organizations(id),
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing.billing_state (
    org_id text PRIMARY KEY REFERENCES core.organizations(id),
    installation_paid_at timestamptz,
    status text NOT NULL CHECK (status IN ('active', 'past_due', 'suspended')),
    grace_until timestamptz,
    last_stripe_event_id text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing.stripe_events (
    event_id text PRIMARY KEY,
    type text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

CREATE TABLE IF NOT EXISTS usage.tokens_daily (
    org_id text NOT NULL REFERENCES core.organizations(id),
    user_id text NOT NULL REFERENCES core.users(id),
    day date NOT NULL,                   -- UTC
    model text NOT NULL,
    tokens_in bigint NOT NULL DEFAULT 0,
    tokens_out bigint NOT NULL DEFAULT 0,
    calls int NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, user_id, day, model)
);

CREATE TABLE IF NOT EXISTS usage.meter_outbox (
    id uuid PRIMARY KEY,                 -- idempotency key
    org_id text NOT NULL REFERENCES core.organizations(id),
    user_id text NOT NULL REFERENCES core.users(id),
    model text NOT NULL,
    tokens_in bigint NOT NULL,
    tokens_out bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz                  -- null = not yet sent
);

CREATE TABLE IF NOT EXISTS runtime.deployments (
    id uuid PRIMARY KEY,
    org_id text NOT NULL REFERENCES core.organizations(id),
    automation_id text NOT NULL,
    fly_app text NOT NULL,
    deployed_by text NOT NULL REFERENCES core.users(id),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    stopped_at timestamptz
);

CREATE TABLE IF NOT EXISTS runtime.runs (
    id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES runtime.deployments(id),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    status text NOT NULL,
    tokens bigint NOT NULL DEFAULT 0,
    error_code text
);

CREATE TABLE IF NOT EXISTS ops.releases (
    component text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('stable', 'beta')),
    version text NOT NULL,
    min_version text NOT NULL,
    assets jsonb NOT NULL,               -- url, sha256, size
    published_at timestamptz NOT NULL,
    PRIMARY KEY (component, channel)
);

CREATE TABLE IF NOT EXISTS ops.maintenance (
    id uuid PRIMARY KEY,
    message_it text NOT NULL,
    message_en text NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    blocking boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS audit.log (
    id bigserial PRIMARY KEY,
    org_id text REFERENCES core.organizations(id),
    actor text NOT NULL,
    action text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------
-- Public read surface for the Husk desktop app: Neon Data API, `anonymous` role.
-- Enable the Data API on the branch, keep `anonymous` as the anonymous role, expose `public`, and enable
-- Neon Auth with anonymous tokens: the Data API accepts only JWTs, the app sends a short-lived anonymous one.
-- Nothing else is granted to `anonymous`: no billing, no core, no usage, no runtime, no audit.
-- The views run with their owner's rights, so `anonymous` needs no access to `ops` itself.
-- ---------------------------------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anonymous') THEN
        CREATE ROLE anonymous NOLOGIN NOBYPASSRLS;  -- normally created by the Data API itself
    END IF;
END $$;

CREATE OR REPLACE VIEW public.husk_maintenance AS
    SELECT id, message_it, message_en, starts_at, ends_at, blocking
    FROM ops.maintenance
    WHERE ends_at > now() - interval '1 day';      -- current and upcoming windows only

CREATE OR REPLACE VIEW public.husk_releases AS
    SELECT component, channel, version, min_version, assets, published_at
    FROM ops.releases;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anonymous;
GRANT USAGE ON SCHEMA public TO anonymous;
GRANT SELECT ON public.husk_maintenance, public.husk_releases TO anonymous;
-- Read-only by construction: no INSERT/UPDATE/DELETE grant exists for `anonymous`.
