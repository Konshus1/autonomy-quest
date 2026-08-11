-- Close-loop queue bridge and fenced shared selector authority.
-- Stock AQ safety: this migration never references TalkingBack's optional task/readiness tables.
BEGIN;

ALTER TABLE public.work
  ADD COLUMN IF NOT EXISTS execution_path text NOT NULL DEFAULT 'mission';
ALTER TABLE public.work
  ADD COLUMN IF NOT EXISTS task_work_link_id bigint;
ALTER TABLE public.work
  ADD COLUMN IF NOT EXISTS parent_work_id bigint;

DO $work_parent_fk$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid='public.work'::regclass AND conname='work_parent_work_fk'
  ) THEN
    ALTER TABLE public.work ADD CONSTRAINT work_parent_work_fk
      FOREIGN KEY(parent_work_id) REFERENCES public.work(id) ON DELETE RESTRICT
      DEFERRABLE INITIALLY DEFERRED;
  END IF;
END $work_parent_fk$;

DO $work_path_check$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid='public.work'::regclass AND conname='work_execution_path_check'
  ) THEN
    ALTER TABLE public.work ADD CONSTRAINT work_execution_path_check
      CHECK (execution_path IN ('mission','worker_reviewer')) NOT VALID;
    ALTER TABLE public.work VALIDATE CONSTRAINT work_execution_path_check;
  END IF;
END $work_path_check$;

CREATE TABLE IF NOT EXISTS public.queue_bridge_observation (
  observation_id bigserial PRIMARY KEY,
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_system text NOT NULL CHECK (btrim(source_system)<>''),
  source_task_id text NOT NULL CHECK (btrim(source_task_id)<>''),
  readiness_status text NOT NULL,
  ready_for_worker_launch boolean NOT NULL,
  cancel_requested boolean NOT NULL,
  source_intent_hash text NOT NULL CHECK (source_intent_hash ~ '^[0-9a-f]{64}$'),
  source_observation_hash text NOT NULL CHECK (source_observation_hash ~ '^[0-9a-f]{64}$'),
  payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload)='object'),
  UNIQUE(source_system,source_task_id,source_observation_hash)
);
CREATE INDEX IF NOT EXISTS queue_bridge_observation_latest_idx
  ON public.queue_bridge_observation(source_system,source_task_id,observed_at DESC,observation_id DESC);

CREATE TABLE IF NOT EXISTS public.queue_dispatch_lease (
  source_system text NOT NULL CHECK (btrim(source_system)<>''),
  source_task_id text NOT NULL CHECK (btrim(source_task_id)<>''),
  owner_system text NOT NULL CHECK (owner_system IN ('aq','ralph')),
  owner_instance text NOT NULL CHECK (btrim(owner_instance)<>''),
  lease_token uuid NOT NULL,
  generation bigint NOT NULL CHECK (generation>0),
  source_intent_hash text NOT NULL CHECK (source_intent_hash ~ '^[0-9a-f]{64}$'),
  claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  lease_until timestamptz NOT NULL,
  PRIMARY KEY(source_system,source_task_id),
  UNIQUE(lease_token)
);

CREATE TABLE IF NOT EXISTS public.task_work_link (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_system text NOT NULL CHECK (btrim(source_system)<>''),
  source_task_id text NOT NULL CHECK (btrim(source_task_id)<>''),
  source_intent_hash text NOT NULL CHECK (source_intent_hash ~ '^[0-9a-f]{64}$'),
  source_observation_hash text NOT NULL CHECK (source_observation_hash ~ '^[0-9a-f]{64}$'),
  mission_hash text NOT NULL CHECK (mission_hash ~ '^[0-9a-f]{64}$'),
  work_id bigint NOT NULL UNIQUE REFERENCES public.work(id) ON DELETE RESTRICT,
  lease_token uuid NOT NULL,
  lease_generation bigint NOT NULL CHECK (lease_generation>0),
  state text NOT NULL DEFAULT 'materialized' CHECK (state IN
    ('eligible','claimed','materialized','reviewing','merge_ready','merged','refused','blocked')),
  last_error text,
  UNIQUE(source_system,source_task_id),
  -- Historical fencing evidence is copied here.  It intentionally is not a foreign
  -- key to the single rotating current-lease row, whose token changes on reclaim.
  CHECK (lease_token IS NOT NULL)
);

-- The redundant work/link ids are a paired identity, not two independent
-- nullable pointers.  Deferral permits an atomic transaction to insert the work,
-- insert its link, and then bind the work before commit.
CREATE UNIQUE INDEX IF NOT EXISTS task_work_link_work_id_id_uidx
  ON public.task_work_link(work_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS work_id_task_work_link_id_uidx
  ON public.work(id,task_work_link_id);
CREATE UNIQUE INDEX IF NOT EXISTS work_task_work_link_id_uidx
  ON public.work(task_work_link_id) WHERE task_work_link_id IS NOT NULL;

DO $work_link_fk$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid='public.work'::regclass AND conname='work_task_work_link_pair_fk'
  ) THEN
    ALTER TABLE public.work ADD CONSTRAINT work_task_work_link_pair_fk
      FOREIGN KEY(id,task_work_link_id)
      REFERENCES public.task_work_link(work_id,id) ON DELETE RESTRICT
      DEFERRABLE INITIALLY DEFERRED;
  END IF;
END $work_link_fk$;

CREATE TABLE IF NOT EXISTS public.merge_attempt (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  task_work_link_id bigint NOT NULL REFERENCES public.task_work_link(id) ON DELETE RESTRICT,
  work_id bigint NOT NULL REFERENCES public.work(id) ON DELETE RESTRICT,
  run_id bigint REFERENCES public.runs(id) ON DELETE RESTRICT,
  attempt_no integer NOT NULL CHECK (attempt_no>0),
  disposition text NOT NULL CHECK (disposition IN
    ('approve_local','approve_public','refuse','escalate_human','landed','failed')),
  reason_code text NOT NULL CHECK (btrim(reason_code)<>''),
  repo_id text NOT NULL CHECK (btrim(repo_id)<>''),
  target_ref text NOT NULL CHECK (btrim(target_ref)<>''),
  base_sha text CHECK (base_sha IS NULL OR base_sha ~ '^[0-9a-f]{40}$'),
  candidate_sha text CHECK (candidate_sha IS NULL OR candidate_sha ~ '^[0-9a-f]{40}$'),
  integration_sha text CHECK (integration_sha IS NULL OR integration_sha ~ '^[0-9a-f]{40}$'),
  observed_remote_sha text CHECK (observed_remote_sha IS NULL OR observed_remote_sha ~ '^[0-9a-f]{40}$'),
  gate_hash text CHECK (gate_hash IS NULL OR gate_hash ~ '^[0-9a-f]{64}$'),
  source_intent_hash text NOT NULL CHECK (source_intent_hash ~ '^[0-9a-f]{64}$'),
  mission_hash text NOT NULL CHECK (mission_hash ~ '^[0-9a-f]{64}$'),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence)='object'),
  UNIQUE(task_work_link_id,attempt_no)
);
CREATE INDEX IF NOT EXISTS merge_attempt_work_idx ON public.merge_attempt(work_id,created_at DESC);

-- One atomic claim primitive for both selectors.  The latest locally recorded source read is
-- rechecked under the same per-task advisory lock: an absent/stale hash, non-exact status,
-- false/null readiness, or cancellation can never produce authority.
CREATE OR REPLACE FUNCTION public.aq_try_claim_queue_dispatch_lease(
  p_source_system text, p_source_task_id text, p_owner_system text,
  p_owner_instance text, p_source_intent_hash text, p_ttl_seconds integer)
RETURNS TABLE(
  decision text, reason_code text, source_system text, source_task_id text,
  owner_system text, owner_instance text, lease_token uuid, generation bigint,
  lease_until timestamptz, source_intent_hash text)
LANGUAGE plpgsql
AS $claim$
DECLARE
  v_obs public.queue_bridge_observation%ROWTYPE;
  v_current public.queue_dispatch_lease%ROWTYPE;
  v_token uuid;
  v_generation bigint;
  v_until timestamptz;
BEGIN
  IF btrim(coalesce(p_source_system,''))='' OR btrim(coalesce(p_source_task_id,''))=''
     OR p_owner_system NOT IN ('aq','ralph') OR btrim(coalesce(p_owner_instance,''))=''
     OR p_source_intent_hash !~ '^[0-9a-f]{64}$' OR p_ttl_seconds NOT BETWEEN 1 AND 3600 THEN
    RAISE EXCEPTION 'invalid queue dispatch lease request';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_source_system||chr(31)||p_source_task_id,0));
  SELECT o.* INTO v_obs FROM public.queue_bridge_observation o
   WHERE o.source_system=p_source_system AND o.source_task_id=p_source_task_id
   ORDER BY o.observed_at DESC,o.observation_id DESC LIMIT 1;
  IF NOT FOUND OR v_obs.readiness_status<>'launch_ready_bounded_dev_safe_slice'
     OR v_obs.ready_for_worker_launch IS NOT TRUE OR v_obs.cancel_requested IS NOT FALSE
     OR v_obs.source_intent_hash<>p_source_intent_hash THEN
    RETURN QUERY SELECT 'ineligible'::text,
      CASE WHEN NOT FOUND THEN 'source_observation_missing'
           WHEN v_obs.cancel_requested THEN 'source_cancelled'
           WHEN v_obs.source_intent_hash<>p_source_intent_hash THEN 'stale_intent_hash_disagreement'
           WHEN v_obs.readiness_status<>'launch_ready_bounded_dev_safe_slice' THEN 'status_not_exactly_launch_ready'
           ELSE 'ready_for_worker_launch_not_true' END,
      p_source_system,p_source_task_id,NULL::text,NULL::text,NULL::uuid,NULL::bigint,
      NULL::timestamptz,NULL::text;
    RETURN;
  END IF;
  SELECT q.* INTO v_current FROM public.queue_dispatch_lease q
   WHERE q.source_system=p_source_system AND q.source_task_id=p_source_task_id FOR UPDATE;
  IF FOUND AND v_current.lease_until>clock_timestamp() THEN
    IF v_current.owner_system=p_owner_system AND v_current.owner_instance=p_owner_instance
       AND v_current.source_intent_hash=p_source_intent_hash THEN
      RETURN QUERY SELECT 'acquired'::text,'lease_already_owned'::text,
        v_current.source_system,v_current.source_task_id,v_current.owner_system,
        v_current.owner_instance,v_current.lease_token,v_current.generation,
        v_current.lease_until,v_current.source_intent_hash;
    ELSE
      RETURN QUERY SELECT 'held'::text,'lease_held'::text,p_source_system,p_source_task_id,
        v_current.owner_system,v_current.owner_instance,NULL::uuid,v_current.generation,
        v_current.lease_until,v_current.source_intent_hash;
    END IF;
    RETURN;
  END IF;
  v_generation:=coalesce(v_current.generation,0)+1;
  v_token:=gen_random_uuid();
  v_until:=clock_timestamp()+make_interval(secs=>p_ttl_seconds);
  INSERT INTO public.queue_dispatch_lease AS q(
    source_system,source_task_id,owner_system,owner_instance,lease_token,generation,
    source_intent_hash,claimed_at,lease_until)
  VALUES(p_source_system,p_source_task_id,p_owner_system,p_owner_instance,v_token,v_generation,
    p_source_intent_hash,clock_timestamp(),v_until)
  ON CONFLICT ON CONSTRAINT queue_dispatch_lease_pkey DO UPDATE SET
    owner_system=excluded.owner_system,owner_instance=excluded.owner_instance,
    lease_token=excluded.lease_token,generation=excluded.generation,
    source_intent_hash=excluded.source_intent_hash,claimed_at=excluded.claimed_at,
    lease_until=excluded.lease_until;
  RETURN QUERY SELECT 'acquired'::text,'lease_acquired'::text,p_source_system,p_source_task_id,
    p_owner_system,p_owner_instance,v_token,v_generation,v_until,p_source_intent_hash;
END
$claim$;

CREATE OR REPLACE FUNCTION public.aq_assert_queue_dispatch_lease(
  p_source_system text,p_source_task_id text,p_owner_system text,p_owner_instance text,
  p_lease_token uuid,p_generation bigint)
RETURNS boolean LANGUAGE sql STABLE AS $assert$
  SELECT EXISTS(
    SELECT 1 FROM public.queue_dispatch_lease q
    WHERE q.source_system=p_source_system AND q.source_task_id=p_source_task_id
      AND q.owner_system=p_owner_system AND q.owner_instance=p_owner_instance
      AND q.lease_token=p_lease_token AND q.generation=p_generation
      AND q.lease_until>clock_timestamp()
      AND TRUE = (
        SELECT o.source_intent_hash=q.source_intent_hash
          AND o.readiness_status='launch_ready_bounded_dev_safe_slice'
          AND o.ready_for_worker_launch IS TRUE AND o.cancel_requested IS FALSE
        FROM public.queue_bridge_observation o
        WHERE o.source_system=q.source_system AND o.source_task_id=q.source_task_id
        ORDER BY o.observed_at DESC,o.observation_id DESC LIMIT 1
      )
  )
$assert$;

CREATE OR REPLACE FUNCTION public.aq_release_queue_dispatch_lease(
  p_source_system text,p_source_task_id text,p_owner_system text,p_owner_instance text,
  p_lease_token uuid,p_generation bigint)
RETURNS boolean LANGUAGE plpgsql AS $release$
DECLARE v_count integer;
BEGIN
  UPDATE public.queue_dispatch_lease q SET lease_until='-infinity'::timestamptz
   WHERE q.source_system=p_source_system AND q.source_task_id=p_source_task_id
     AND q.owner_system=p_owner_system AND q.owner_instance=p_owner_instance
     AND q.lease_token=p_lease_token AND q.generation=p_generation
     AND q.lease_until>clock_timestamp();
  GET DIAGNOSTICS v_count=ROW_COUNT;
  RETURN v_count=1;
END
$release$;

COMMIT;
