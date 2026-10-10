-- Data-minimisation CHECK constraints (run as owner so RLS is not the reason).
begin;
\ir _helpers.psql
select plan(20);

select throws_ok($$ insert into public.child_profile (id, owner_id, birth_year, birth_month, birth_day)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 2017, 5, 12) $$,
                 '42703', null, 'there is no column for a day of birth (no exact DOB)');
select throws_ok($$ insert into public.child_profile (id, owner_id, full_name) values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 'x') $$,
                 '42703', null, 'there is no name column');
select throws_ok($$ insert into public.child_profile (id, owner_id, nik) values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', '3201') $$,
                 '42703', null, 'there is no NIK column');
select throws_ok($$ insert into public.child_profile (id, owner_id, birth_year, birth_month)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 2017, 13) $$,
                 '23514', null, 'birth_month must be 1..12');
select throws_ok($$ insert into public.child_profile (id, owner_id, birth_year)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 1990) $$,
                 '23514', null, 'birth_year restricted to the school-age window');
select throws_ok($$ insert into public.child_profile (id, owner_id, birth_month)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 4) $$,
                 '23514', null, 'birth_month requires birth_year');
select throws_ok($$ insert into public.child_profile (id, owner_id, allergies)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', array['Alergi kacang - Ibu Siti']) $$,
                 '23514', null, 'allergies accept codes only (no free text)');
select lives_ok($$ insert into public.child_profile (id, owner_id, birth_year, birth_month, allergies)
                   values ('00000000-0000-4000-8000-00000000ca05', '00000000-0000-4000-8000-0000000000a1', 2017, 5, array['mint', 'sls']) $$,
                'a valid pseudonymous profile is accepted');

insert into public.class_roster (id, kader_id, label) values
  ('00000000-0000-4000-8000-0000000f0005', '00000000-0000-4000-8000-0000000000c1', 'Kelas 4A');
select throws_ok($$ insert into public.class_roster_member (roster_id, child_id, member_label)
                    values ('00000000-0000-4000-8000-0000000f0005', '00000000-0000-4000-8000-00000000ca05', 'Budi') $$,
                 '23514', null, 'roster member labels cannot be names');
select lives_ok($$ insert into public.class_roster_member (roster_id, child_id, member_label)
                   values ('00000000-0000-4000-8000-0000000f0005', '00000000-0000-4000-8000-00000000ca05', 'A-07') $$,
                'pseudonymous member label A-07 is accepted');
select throws_ok($$ insert into public.class_roster (kader_id, label) values ('00000000-0000-4000-8000-0000000000c1', '<script>') $$,
                 '23514', null, 'roster label is constrained');

select throws_ok($$ insert into public.screening (id, child_id, screened_at, model_version, rules_version, level, findings)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-00000000ca05', now(), '1.0.0', '1.0.0', 'green',
                            '[{"class": "caries_permanent", "confidence": 1.5}]') $$,
                 '23514', null, 'finding confidence must be within 0..1');
select throws_ok($$ insert into public.screening (id, child_id, screened_at, model_version, rules_version, level, findings)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-00000000ca05', now(), '1.0.0', '1.0.0', 'green',
                            '[{"class": "caries_permanent", "image_b64": "AAAA"}]') $$,
                 '23514', null, 'findings cannot carry extra fields (e.g. image data)');
select throws_ok($$ insert into public.screening (id, child_id, screened_at, model_version, rules_version, level)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-00000000ca05', now(), 'latest', '1.0.0', 'green') $$,
                 '23514', null, 'model_version must be a version tag');

select throws_ok($$ insert into public.audit_log (actor_role, action, details)
                    values ('admin', 'test', '{"child": "00000000-0000-4000-8000-00000000ca05"}') $$,
                 '23514', null, 'audit_log details reject UUID-shaped values');
select throws_ok($$ insert into public.audit_log (actor_role, action, details)
                    values ('admin', 'test', '{"note": "Anak Budi demam"}') $$,
                 '23514', null, 'audit_log details reject free text');

insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
values ('00000000-0000-4000-8000-00000000ca05', '00000000-0000-4000-8000-0000000000a1', 'b', 'v1', now());
select throws_ok($$ insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
                    values ('00000000-0000-4000-8000-00000000ca05', '00000000-0000-4000-8000-0000000000a1', 'b', 'v1', now()) $$,
                 '23505', null, 'at most one ACTIVE consent per child and type');
select throws_ok($$ insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
                    values ('00000000-0000-4000-8000-00000000ca05', '00000000-0000-4000-8000-0000000000a1', 'c', 'versi teks?', now()) $$,
                 '23514', null, 'consent text_version is a version code');

select throws_ok($$ insert into public.image_upload (id, child_id, object_path)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-00000000ca05', 'photos/budi.png') $$,
                 '23514', null, 'image object path must be <uid>/<child>/<uuid>.jpg');
select throws_ok($$ insert into public.brushing_event (id, child_id, started_at, duration_s)
                    values (gen_random_uuid(), '00000000-0000-4000-8000-00000000ca05', now(), 99999) $$,
                 '23514', null, 'brushing duration is bounded');

select * from finish();
rollback;
