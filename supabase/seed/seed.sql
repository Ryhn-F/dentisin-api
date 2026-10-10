-- =============================================================================
-- DentisAI · SYNTHETIC seed data for local development (`supabase db reset`).
-- Everything here is FAKE: e-mails use the reserved .invalid TLD, UUIDs follow
-- an obvious 5eed… pattern, there are no names, no NIK, no photos and no
-- passwords (seeded accounts cannot sign in; use the local Inbucket signup
-- flow for interactive testing). Never run this against a hosted project.
-- =============================================================================

-- Accounts (the signup trigger creates guardian rows + the 'guardian' role)
insert into auth.users (id, aud, role, email, email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
values
  ('5eed0000-0000-4000-8000-00000000a001', 'authenticated', 'authenticated', 'synthetic-parent-1@example.invalid', now(), '{}', '{}'),
  ('5eed0000-0000-4000-8000-00000000a002', 'authenticated', 'authenticated', 'synthetic-parent-2@example.invalid', now(), '{}', '{}'),
  ('5eed0000-0000-4000-8000-00000000b001', 'authenticated', 'authenticated', 'synthetic-kader-1@example.invalid', now(), '{}', '{}'),
  ('5eed0000-0000-4000-8000-00000000c001', 'authenticated', 'authenticated', 'synthetic-puskesmas-1@example.invalid', now(), '{}', '{}'),
  ('5eed0000-0000-4000-8000-00000000d001', 'authenticated', 'authenticated', 'synthetic-admin-1@example.invalid', now(), '{}', '{}')
on conflict (id) do nothing;

-- Roles beyond the default (seed runs as the owner; clients can never do this)
insert into public.user_role (user_id, role, facility_code) values
  ('5eed0000-0000-4000-8000-00000000b001', 'kader', 'PKM-DEMO-01'),
  ('5eed0000-0000-4000-8000-00000000c001', 'facility_viewer', 'PKM-DEMO-01'),
  ('5eed0000-0000-4000-8000-00000000d001', 'admin', null)
on conflict (user_id, role) do nothing;
delete from public.user_role
 where user_id in ('5eed0000-0000-4000-8000-00000000c001', '5eed0000-0000-4000-8000-00000000d001')
   and role = 'guardian';

-- Children: 2 owned by parents, 8 owned by the kader (class mode)
insert into public.child_profile (id, owner_id, birth_year, birth_month, allergies) values
  ('5eed0000-0000-4000-8000-0000000c0001', '5eed0000-0000-4000-8000-00000000a001', 2017, 3, '{}'),
  ('5eed0000-0000-4000-8000-0000000c0002', '5eed0000-0000-4000-8000-00000000a002', 2016, 11, '{mint}')
on conflict (id) do nothing;

insert into public.child_profile (id, owner_id, birth_year, birth_month)
select ('5eed0000-0000-4000-8000-0000000e' || lpad(g::text, 4, '0'))::uuid,
       '5eed0000-0000-4000-8000-00000000b001', 2016, (g % 12) + 1
from generate_series(1, 8) g
on conflict (id) do nothing;

-- Consent (a) + (b) for every child; nobody has (c) and the flag stays OFF
insert into public.consent (child_id, granted_by, consent_type, text_version, method, granted_at)
select c.id, c.owner_id, t.ct::public.consent_type, 'synthetic-v0',
       case when c.owner_id = '5eed0000-0000-4000-8000-00000000b001' then 'paper_via_kader' else 'in_app' end::public.consent_method,
       now() - interval '30 days'
from public.child_profile c
cross join (values ('a'), ('b')) as t(ct)
where c.id::text like '5eed0000-%'
on conflict do nothing;

-- Kader roster
insert into public.class_roster (id, kader_id, label, school_year, facility_code) values
  ('5eed0000-0000-4000-8000-0000000f0001', '5eed0000-0000-4000-8000-00000000b001', 'Kelas 4A', '2026/2027', 'PKM-DEMO-01')
on conflict (id) do nothing;

insert into public.class_roster_member (roster_id, child_id, member_label)
select '5eed0000-0000-4000-8000-0000000f0001', c.id, 'A-' || lpad(row_number() over (order by c.id)::text, 2, '0')
from public.child_profile c
where c.owner_id = '5eed0000-0000-4000-8000-00000000b001'
on conflict do nothing;

-- Screenings (synthetic levels; model/rules versions are placeholders)
insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level, red_flag, findings)
select ('5eed0000-0000-4000-8000-0000005c' || lpad(row_number() over (order by c.id)::text, 4, '0'))::uuid,
       c.id, c.owner_id, date_trunc('month', now()) + interval '2 days',
       '0.0.0-synthetic', '0.0.0-synthetic',
       (array['green', 'yellow', 'orange', 'green']::public.triage_level[])[1 + (row_number() over (order by c.id))::int % 4],
       false,
       '[{"class": "caries_primary", "confidence": 0.5, "box": [0.4, 0.4, 0.05, 0.05]}]'::jsonb
from public.child_profile c
where c.id::text like '5eed0000-%'
on conflict (id) do nothing;

insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s, spirit_score, slot, camera_mode) values
  ('5eed0000-0000-4000-8000-0000000b0001', '5eed0000-0000-4000-8000-0000000c0001',
   '5eed0000-0000-4000-8000-00000000a001', now() - interval '1 day', 125, 80, 'evening', true)
on conflict (id) do nothing;
