-- ADR 014: ml_review_result boxes use caries_permanent / caries_primary (annotation
-- only); legacy cavitated/noncavitated names and any box-size-derived field are rejected.
begin;
\ir _helpers.psql
select plan(18);

-- constraint swapped and validated; RLS still enabled + forced -----------------
select is((select convalidated from pg_constraint
           where conname = 'ml_review_result_boxes_valid' and conrelid = 'public.ml_review_result'::regclass),
          true, 'boxes constraint exists and is VALIDATED');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.ml_review_result'::regclass),
          'ml_review_result keeps RLS ENABLED + FORCED');
select ok(not has_function_privilege('authenticated', 'private.ml_boxes_valid(jsonb,integer,integer)', 'EXECUTE')
          and has_function_privilege('service_role', 'private.ml_boxes_valid(jsonb,integer,integer)', 'EXECUTE'),
          'validator grants unchanged (service_role only)');

-- validator, pure -------------------------------------------------------------
select ok(private.ml_boxes_valid('[{"cls":"caries_permanent","x1":10,"y1":20.5,"x2":50,"y2":70}]', 100, 100),
          'caries_permanent (D) accepted');
select ok(private.ml_boxes_valid('[{"cls":"caries_primary","x1":10,"y1":20,"x2":50,"y2":70}]', 100, 100),
          'caries_primary (d) accepted');
select ok(not private.ml_boxes_valid('[{"cls":"caries_cavitated","x1":10,"y1":20,"x2":50,"y2":70}]', 100, 100),
          'legacy caries_cavitated rejected');
select ok(not private.ml_boxes_valid('[{"cls":"caries_noncavitated","x1":10,"y1":20,"x2":50,"y2":70}]', 100, 100),
          'legacy caries_noncavitated rejected');
select ok(not private.ml_boxes_valid('[{"cls":"abscess_fistula","x1":10,"y1":20,"x2":50,"y2":70}]', 100, 100),
          'other classes rejected');
select ok(not private.ml_boxes_valid('[{"cls":1,"x1":10,"y1":20,"x2":50,"y2":70}]', 100, 100),
          'non-string cls rejected');
select ok(not private.ml_boxes_valid('[{"cls":"caries_primary","x1":10,"y1":20,"x2":50,"y2":70,"rel_area":0.1}]', 100, 100),
          'no area/severity key on boxes');
select ok(not private.ml_boxes_valid('[{"cls":"caries_primary","x1":10,"y1":20,"x2":500,"y2":70}]', 100, 100),
          'geometry rules unchanged (x2 <= width)');

-- screening findings: no derived area ----------------------------------------
select ok(private.findings_valid('[{"class":"caries_primary","confidence":0.5,"box":[0.1,0.2,0.05,0.04]}]'),
          'findings: class + confidence + box accepted');
select ok(not private.findings_valid('[{"class":"caries_primary","confidence":0.5,"rel_area":0.02}]'),
          'findings: rel_area (box-size-derived) is rejected (ADR 014)');

-- end-to-end through the real table (owner fixture; triggers + CHECK live) -----
select tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca09',
                        array['a', 'b', 'c']);
update public.feature_flag set enabled = true, approval_ref = 'TEST-KEPK-9' where key = 'dataset_contribution';
insert into public.image_upload (id, child_id, uploaded_by, object_path, expires_at)
values ('89000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-00000000ca09',
        '00000000-0000-4000-8000-0000000000a1',
        '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca09/89000000-0000-4000-8000-000000000001.jpg',
        now() + interval '30 days');
insert into public.ml_review_queue (id, image_upload_id, required_reviews)
values ('89100000-0000-4000-8000-000000000001', '89000000-0000-4000-8000-000000000001', 3);

select tests.as_service();
select lives_ok($$ insert into public.ml_review_result (queue_id, grader_pseudonym, decision, image_width, image_height, boxes)
                   values ('89100000-0000-4000-8000-000000000001', 'drg-01', 'annotated', 640, 480,
                           '[{"cls":"caries_permanent","x1":10,"y1":20,"x2":50,"y2":70},{"cls":"caries_primary","x1":60,"y1":20,"x2":90,"y2":70}]') $$,
                'service_role stores a review with both new classes');
select throws_ok($$ insert into public.ml_review_result (queue_id, grader_pseudonym, decision, image_width, image_height, boxes)
                    values ('89100000-0000-4000-8000-000000000001', 'drg-02', 'annotated', 640, 480,
                            '[{"cls":"caries_cavitated","x1":10,"y1":20,"x2":50,"y2":70}]') $$,
                 '23514', null, 'insert with legacy caries_cavitated violates the CHECK');
select throws_ok($$ insert into public.ml_review_result (queue_id, grader_pseudonym, decision, image_width, image_height, boxes)
                    values ('89100000-0000-4000-8000-000000000001', 'drg-03', 'annotated', 640, 480,
                            '[{"cls":"caries_noncavitated","x1":10,"y1":20,"x2":50,"y2":70}]') $$,
                 '23514', null, 'insert with legacy caries_noncavitated violates the CHECK');
select lives_ok($$ insert into public.ml_review_result (queue_id, grader_pseudonym, decision, image_width, image_height, boxes)
                   values ('89100000-0000-4000-8000-000000000001', 'drg-04', 'no_finding', 640, 480, '[]') $$,
                'no_finding with an empty box list still works');

-- migration safety: validation step aborts clearly when a legacy row exists ------
select tests.as_owner();
alter table public.ml_review_result drop constraint ml_review_result_boxes_valid;
insert into public.ml_review_result (queue_id, grader_pseudonym, decision, image_width, image_height, boxes)
values ('89100000-0000-4000-8000-000000000001', 'drg-legacy', 'annotated', 640, 480,
        '[{"cls":"caries_cavitated","x1":10,"y1":20,"x2":50,"y2":70}]');
select is((select count(*) from public.ml_review_result r
           where not private.ml_boxes_valid(r.boxes, r.image_width, r.image_height))::int, 1,
          'a legacy row would be detected by the migration guard (count of violators = 1)');

select * from finish();
rollback;
