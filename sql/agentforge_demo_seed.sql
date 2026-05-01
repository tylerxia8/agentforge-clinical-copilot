-- agentforge_demo_seed.sql
--
-- Seeds the 14 demo patients in sql/example_patient_data.sql with
-- clinically realistic problem lists, medications, allergies, and
-- encounters. Designed for the AgentForge co-pilot demo so the
-- agent has rich data to surface across UC-1 / UC-2 / UC-3.
--
-- Idempotent-ish: clears any prior co-pilot-seeded clinical data
-- for these pids before re-inserting (so re-running this file is
-- safe). Patient demographics rows are NOT touched.
--
-- Encounter ID scheme: 2000 + pid*10 + N (avoids collision with
-- Farrah's pre-existing 1000/1001).
--
-- Run via:
--   docker run --rm -i mysql:8 mysql -h<host> -P<port> -u<user> -p<pass> \
--     <db> < sql/agentforge_demo_seed.sql

SET SESSION sql_mode = '';

-- ─── Wipe any previous co-pilot seed (so this file is re-runnable) ─────
DELETE FROM prescriptions WHERE patient_id IN (1,4,8,17,18,22,25,26,30,34,35,40,41);
DELETE FROM lists WHERE pid IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND modifydate >= '2026-04-30';
DELETE FROM forms WHERE pid IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND encounter BETWEEN 2000 AND 2999;
DELETE FROM form_encounter WHERE pid IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND encounter BETWEEN 2000 AND 2999;

-- ─── Ted Shaw (pid=1, 79yo M) — BPH + HTN + hyperlipidemia ─────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (1, 'Tamsulosin',   '0.4',  'PO', 1, 'Take 0.4 mg by mouth once daily',           1, '2024-09-15', '316049', 0, 1, NOW(), '2024-09-15'),
  (1, 'Lisinopril',   '10',   'PO', 1, 'Take 10 mg by mouth once daily',            1, '2023-06-01', '29046',  0, 1, NOW(), '2023-06-01'),
  (1, 'Atorvastatin', '20',   'PO', 1, 'Take 20 mg by mouth at bedtime',            1, '2023-06-01', '83367',  0, 1, NOW(), '2023-06-01'),
  (1, 'Aspirin',      '81',   'PO', 1, 'Take 81 mg (low-dose) by mouth once daily', 1, '2023-06-01', '243670', 0, 1, NOW(), '2023-06-01');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (1, 'medical_problem', 'Benign prostatic hyperplasia',           'ICD10:N40.0', 1, '2024-09-10', NOW()),
  (1, 'medical_problem', 'Essential (primary) hypertension',       'ICD10:I10',   1, '2023-05-20', NOW()),
  (1, 'medical_problem', 'Hyperlipidemia, unspecified',            'ICD10:E78.5', 1, '2023-05-20', NOW());
-- No known drug allergies; OpenEMR convention: a single 'NKDA' row
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (1, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── Eduardo Perez (pid=4, 69yo M) — T2DM + CAD + hyperlipidemia ───────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (4, 'Metformin',     '1000', 'PO', 2, 'Take 1000 mg by mouth twice daily with meals', 1, '2022-03-12', '860975', 0, 1, NOW(), '2022-03-12'),
  (4, 'Carvedilol',    '12.5', 'PO', 2, 'Take 12.5 mg by mouth twice daily',             1, '2024-01-08', '200033', 0, 1, NOW(), '2024-01-08'),
  (4, 'Atorvastatin',  '40',   'PO', 1, 'Take 40 mg by mouth at bedtime',                1, '2024-01-08', '83367',  0, 1, NOW(), '2024-01-08'),
  (4, 'Aspirin',       '81',   'PO', 1, 'Take 81 mg by mouth once daily',                1, '2024-01-08', '243670', 0, 1, NOW(), '2024-01-08');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (4, 'medical_problem', 'Type 2 diabetes mellitus without complications', 'ICD10:E11.9', 1, '2022-03-01', NOW()),
  (4, 'medical_problem', 'Atherosclerotic heart disease of native coronary artery without angina', 'ICD10:I25.10', 1, '2024-01-05', NOW()),
  (4, 'medical_problem', 'Hyperlipidemia, unspecified',                    'ICD10:E78.5', 1, '2024-01-05', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (4, 'allergy', 'Sulfa antibiotics', 'Rash', 'moderate', 'confirmed', 1, NOW());

-- ─── Nora Cohen (pid=8, 59yo F) — Hypothyroidism + osteopenia ──────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (8, 'Levothyroxine',     '75', 'PO', 1, 'Take 75 mcg by mouth once daily on an empty stomach', 1, '2018-11-05', '966247', 0, 1, NOW(), '2018-11-05'),
  (8, 'Cholecalciferol',   '2000', 'PO', 1, 'Take 2000 IU (Vitamin D3) by mouth once daily',     1, '2023-04-01', '105520', 0, 1, NOW(), '2023-04-01'),
  (8, 'Calcium carbonate', '1200', 'PO', 1, 'Take 1200 mg by mouth once daily',                  1, '2023-04-01', '1366221', 0, 1, NOW(), '2023-04-01');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (8, 'medical_problem', 'Hypothyroidism, unspecified', 'ICD10:E03.9', 1, '2018-10-22', NOW()),
  (8, 'medical_problem', 'Osteopenia',                  'ICD10:M85.80', 1, '2023-03-15', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (8, 'allergy', 'Latex', 'Contact dermatitis', 'mild', 'confirmed', 1, NOW());

-- ─── Jim Moses (pid=17, 81yo M) — Atrial fibrillation + CHF + HTN ──────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (17, 'Apixaban',     '5',   'PO', 2, 'Take 5 mg by mouth twice daily',                     1, '2023-08-14', '1364430', 0, 1, NOW(), '2023-08-14'),
  (17, 'Furosemide',   '40',  'PO', 1, 'Take 40 mg by mouth once daily in the morning',      1, '2024-02-20', '4603',    0, 1, NOW(), '2024-02-20'),
  (17, 'Metoprolol succinate', '50', 'PO', 1, 'Take 50 mg (extended release) by mouth once daily', 1, '2023-08-14', '866427', 0, 1, NOW(), '2023-08-14'),
  (17, 'Lisinopril',   '20',  'PO', 1, 'Take 20 mg by mouth once daily',                     1, '2020-01-10', '29046',   0, 1, NOW(), '2020-01-10');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (17, 'medical_problem', 'Atrial fibrillation, unspecified',            'ICD10:I48.91',  1, '2023-08-10', NOW()),
  (17, 'medical_problem', 'Heart failure, unspecified',                  'ICD10:I50.9',   1, '2024-02-15', NOW()),
  (17, 'medical_problem', 'Essential (primary) hypertension',            'ICD10:I10',     1, '2019-12-22', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (17, 'allergy', 'Iodinated contrast media', 'Anaphylaxis', 'severe', 'confirmed', 1, NOW());

-- ─── Richard Jones (pid=18, 86yo) — COPD + GERD ────────────────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (18, 'Tiotropium',         '18',   'INH', 1, 'Inhale 18 mcg via Handihaler once daily',           1, '2021-05-01', '386102', 0, 1, NOW(), '2021-05-01'),
  (18, 'Albuterol HFA',       '90',   'INH', 4, 'Inhale 2 puffs (90 mcg/puff) every 4-6 hours as needed for shortness of breath', 1, '2021-05-01', '435', 0, 1, NOW(), '2021-05-01'),
  (18, 'Omeprazole',          '20',   'PO',  1, 'Take 20 mg by mouth once daily before breakfast',  1, '2022-09-12', '7646',   0, 1, NOW(), '2022-09-12');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (18, 'medical_problem', 'Chronic obstructive pulmonary disease, unspecified', 'ICD10:J44.9', 1, '2021-04-25', NOW()),
  (18, 'medical_problem', 'Gastro-esophageal reflux disease without esophagitis', 'ICD10:K21.9', 1, '2022-09-10', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (18, 'allergy', 'Penicillin', 'Hives', 'moderate', 'confirmed', 1, NOW());

-- ─── Ilias Jenane (pid=22, 93yo F) — Alzheimer's + HTN ─────────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (22, 'Donepezil',  '10', 'PO', 1, 'Take 10 mg by mouth at bedtime',          1, '2022-11-20', '997220', 0, 1, NOW(), '2022-11-20'),
  (22, 'Memantine',  '10', 'PO', 2, 'Take 10 mg by mouth twice daily',         1, '2024-03-08', '702983', 0, 1, NOW(), '2024-03-08'),
  (22, 'Amlodipine', '5',  'PO', 1, 'Take 5 mg by mouth once daily',           1, '2018-07-04', '17767',  0, 1, NOW(), '2018-07-04');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (22, 'medical_problem', 'Alzheimer''s disease, unspecified', 'ICD10:G30.9', 1, '2022-11-15', NOW()),
  (22, 'medical_problem', 'Essential (primary) hypertension',  'ICD10:I10',   1, '2018-06-30', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (22, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── John Dockerty (pid=25, 49yo) — GAD + MDD ──────────────────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (25, 'Sertraline',  '100', 'PO', 1, 'Take 100 mg by mouth once daily in the morning',  1, '2024-06-15', '321988', 0, 1, NOW(), '2024-06-15'),
  (25, 'Hydroxyzine', '25',  'PO', 4, 'Take 25 mg by mouth every 6 hours as needed for anxiety', 1, '2024-06-15', '5708',   0, 1, NOW(), '2024-06-15');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (25, 'medical_problem', 'Generalized anxiety disorder',           'ICD10:F41.1', 1, '2024-06-10', NOW()),
  (25, 'medical_problem', 'Major depressive disorder, recurrent, moderate', 'ICD10:F33.1', 1, '2024-06-10', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (25, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── James Janssen (pid=26, 60yo M) — HTN + pre-diabetes ───────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (26, 'Lisinopril', '20',  'PO', 1, 'Take 20 mg by mouth once daily',                       1, '2023-02-14', '29046',  0, 1, NOW(), '2023-02-14'),
  (26, 'Metformin',  '500', 'PO', 2, 'Take 500 mg by mouth twice daily with meals',          1, '2024-01-22', '860975', 0, 1, NOW(), '2024-01-22'),
  (26, 'Hydrochlorothiazide', '25', 'PO', 1, 'Take 25 mg by mouth once daily in the morning', 1, '2023-02-14', '5487',   0, 1, NOW(), '2023-02-14');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (26, 'medical_problem', 'Essential (primary) hypertension',                  'ICD10:I10',     1, '2023-02-10', NOW()),
  (26, 'medical_problem', 'Pre-diabetes (impaired fasting glucose)',           'ICD10:R73.03',  1, '2024-01-15', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (26, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── Jason Binder (pid=30, 65yo M) — Post-CVA + HTN + hyperlipidemia ───
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (30, 'Apixaban',      '5',   'PO', 2, 'Take 5 mg by mouth twice daily',     1, '2025-01-12', '1364430', 0, 1, NOW(), '2025-01-12'),
  (30, 'Atorvastatin',  '80',  'PO', 1, 'Take 80 mg by mouth at bedtime',     1, '2025-01-12', '83367',   0, 1, NOW(), '2025-01-12'),
  (30, 'Lisinopril',    '40',  'PO', 1, 'Take 40 mg by mouth once daily',     1, '2022-08-01', '29046',   0, 1, NOW(), '2022-08-01');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (30, 'medical_problem', 'Cerebral infarction, unspecified (history of)',   'ICD10:I63.9',   1, '2025-01-08', NOW()),
  (30, 'medical_problem', 'Essential (primary) hypertension',                'ICD10:I10',     1, '2022-07-25', NOW()),
  (30, 'medical_problem', 'Hyperlipidemia, unspecified',                     'ICD10:E78.5',   1, '2022-07-25', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (30, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── Robert Dickey (pid=34, 71yo) — CKD stage 3 + HTN ──────────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (34, 'Lisinopril',    '10', 'PO', 1, 'Take 10 mg by mouth once daily',                  1, '2020-04-10', '29046',  0, 1, NOW(), '2020-04-10'),
  (34, 'Furosemide',    '20', 'PO', 1, 'Take 20 mg by mouth once daily in the morning',   1, '2023-11-05', '4603',   0, 1, NOW(), '2023-11-05'),
  (34, 'Sevelamer',     '800', 'PO', 3, 'Take 800 mg by mouth three times daily with meals', 1, '2024-02-12', '202955', 0, 1, NOW(), '2024-02-12');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (34, 'medical_problem', 'Chronic kidney disease, stage 3 (moderate)',   'ICD10:N18.30', 1, '2023-11-01', NOW()),
  (34, 'medical_problem', 'Essential (primary) hypertension',             'ICD10:I10',    1, '2020-04-05', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (34, 'allergy', 'NSAIDs (ibuprofen, naproxen)', 'Worsening renal function', 'severe', 'confirmed', 1, NOW());

-- ─── Jillian Mahoney (pid=35, 58yo F) — Asthma + allergic rhinitis ─────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (35, 'Fluticasone-Salmeterol', '250-50', 'INH', 2, 'Inhale 1 puff (250/50 mcg) twice daily',                1, '2019-09-20', '895994', 0, 1, NOW(), '2019-09-20'),
  (35, 'Albuterol HFA',          '90',     'INH', 4, 'Inhale 2 puffs every 4-6 hours as needed for wheezing', 1, '2019-09-20', '435',    0, 1, NOW(), '2019-09-20'),
  (35, 'Loratadine',             '10',     'PO',  1, 'Take 10 mg by mouth once daily',                        1, '2021-04-01', '1011478', 0, 1, NOW(), '2021-04-01');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (35, 'medical_problem', 'Mild persistent asthma, uncomplicated', 'ICD10:J45.30', 1, '2019-09-15', NOW()),
  (35, 'medical_problem', 'Allergic rhinitis, unspecified',         'ICD10:J30.9',  1, '2021-03-25', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (35, 'allergy', 'Penicillin', 'Rash', 'moderate', 'confirmed', 1, NOW());

-- ─── Wallace Buckley (pid=40, 74yo) — Parkinson's disease ──────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (40, 'Levodopa-Carbidopa',  '25-100', 'PO', 4, 'Take 1 tablet (25 mg/100 mg) by mouth four times daily',  1, '2022-06-18', '197930', 0, 1, NOW(), '2022-06-18'),
  (40, 'Rasagiline',          '1',      'PO', 1, 'Take 1 mg by mouth once daily',                           1, '2024-01-30', '746023', 0, 1, NOW(), '2024-01-30');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (40, 'medical_problem', 'Parkinson''s disease',                'ICD10:G20', 1, '2022-06-10', NOW()),
  (40, 'medical_problem', 'Constipation, unspecified',           'ICD10:K59.00', 1, '2023-08-04', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (40, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── Brent Perez (pid=41, 66yo M) — T2DM with neuropathy ───────────────
INSERT INTO prescriptions (patient_id, drug, dosage, route, `interval`, drug_dosage_instructions, active, start_date, rxnorm_drugcode, encounter, provider_id, date_added, txDate) VALUES
  (41, 'Metformin',         '1000', 'PO', 2, 'Take 1000 mg by mouth twice daily with meals',                 1, '2020-05-12', '860975',  0, 1, NOW(), '2020-05-12'),
  (41, 'Insulin glargine',  '24',   'SC', 1, 'Inject 24 units subcutaneously once daily at bedtime',         1, '2024-08-22', '847239',  0, 1, NOW(), '2024-08-22'),
  (41, 'Gabapentin',        '300',  'PO', 3, 'Take 300 mg by mouth three times daily for neuropathic pain', 1, '2024-08-22', '25480',   0, 1, NOW(), '2024-08-22'),
  (41, 'Empagliflozin',     '10',   'PO', 1, 'Take 10 mg by mouth once daily',                               1, '2024-12-04', '1545653', 0, 1, NOW(), '2024-12-04');
INSERT INTO lists (pid, type, title, diagnosis, activity, begdate, modifydate) VALUES
  (41, 'medical_problem', 'Type 2 diabetes mellitus with diabetic neuropathy, unspecified', 'ICD10:E11.40', 1, '2024-08-15', NOW()),
  (41, 'medical_problem', 'Type 2 diabetes mellitus with hyperglycemia',                    'ICD10:E11.65', 1, '2020-05-08', NOW());
INSERT INTO lists (pid, type, title, reaction, severity_al, verification, activity, modifydate) VALUES
  (41, 'allergy', 'No known drug allergies', '', 'mild', 'confirmed', 1, NOW());

-- ─── Encounters (2 per patient: one ~6 months ago + one recent) ────────
-- Encounter ID = 2000 + pid*10 + N where N=0 (recent) or 1 (older)
INSERT INTO form_encounter (date, reason, pid, encounter, onset_date, provider_id) VALUES
  ('2026-04-22 09:30:00', 'BPH symptom check; review PSA',                                1,  2010, '2026-04-22', 1),
  ('2025-10-08 11:00:00', 'Annual physical; HTN/lipid review',                            1,  2011, '2025-10-08', 1),
  ('2026-04-18 10:00:00', 'Diabetes follow-up; A1c check; cardiology coordination',       4,  2040, '2026-04-18', 1),
  ('2025-11-12 14:30:00', 'Post-MI follow-up at 6 months',                                4,  2041, '2025-11-12', 1),
  ('2026-04-10 09:00:00', 'Thyroid level recheck; discuss DEXA results',                  8,  2080, '2026-04-10', 1),
  ('2025-10-20 15:00:00', 'Annual well-woman exam',                                       8,  2081, '2025-10-20', 1),
  ('2026-04-25 10:30:00', 'Atrial fibrillation rate control; INR n/a (on apixaban)',     17,  2170, '2026-04-25', 1),
  ('2025-12-05 11:30:00', 'CHF exacerbation follow-up; weight stable',                   17,  2171, '2025-12-05', 1),
  ('2026-04-08 13:00:00', 'COPD stable; flu shot administered',                          18,  2180, '2026-04-08', 1),
  ('2025-09-30 09:30:00', 'COPD annual review; pulmonary function trending stable',     18,  2181, '2025-09-30', 1),
  ('2026-04-20 14:00:00', 'Memory care follow-up; medication reconciliation with caregiver', 22, 2220, '2026-04-20', 1),
  ('2025-11-08 10:00:00', 'Cognitive assessment; MoCA 18/30',                            22,  2221, '2025-11-08', 1),
  ('2026-04-12 16:00:00', 'Mood follow-up; Sertraline tolerability review',              25,  2250, '2026-04-12', 1),
  ('2025-12-15 10:00:00', 'Initial psychiatric consultation',                            25,  2251, '2025-12-15', 1),
  ('2026-04-15 09:00:00', 'BP and pre-diabetes follow-up; metformin titration',          26,  2260, '2026-04-15', 1),
  ('2025-10-25 11:00:00', 'Annual physical; A1c borderline',                             26,  2261, '2025-10-25', 1),
  ('2026-04-19 13:30:00', 'Post-stroke 90-day check; secondary prevention review',       30,  2300, '2026-04-19', 1),
  ('2025-10-15 11:00:00', 'New patient visit; stroke rehab progress',                    30,  2301, '2025-10-15', 1),
  ('2026-04-22 15:00:00', 'CKD stage 3 monitoring; eGFR trending',                       34,  2340, '2026-04-22', 1),
  ('2025-11-18 09:30:00', 'Renal/HTN check; phosphate elevated',                         34,  2341, '2025-11-18', 1),
  ('2026-04-14 10:30:00', 'Asthma well-controlled; refill ICS-LABA',                     35,  2350, '2026-04-14', 1),
  ('2025-10-30 14:00:00', 'Spring allergy flare; loratadine added',                      35,  2351, '2025-10-30', 1),
  ('2026-04-17 11:00:00', 'Parkinson''s motor symptom review; rasagiline tolerability',  40,  2400, '2026-04-17', 1),
  ('2025-12-02 10:00:00', 'Movement disorder follow-up; UPDRS stable',                   40,  2401, '2025-12-02', 1),
  ('2026-04-24 09:00:00', 'Diabetes/neuropathy review; insulin titration',               41,  2410, '2026-04-24', 1),
  ('2025-11-20 13:00:00', 'A1c 8.4 → escalate insulin; gabapentin started',              41,  2411, '2025-11-20', 1);

UPDATE prescriptions SET uuid = UNHEX(REPLACE(UUID(),'-','')) WHERE patient_id IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND uuid IS NULL;
UPDATE lists         SET uuid = UNHEX(REPLACE(UUID(),'-','')) WHERE pid        IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND uuid IS NULL;
UPDATE form_encounter SET uuid = UNHEX(REPLACE(UUID(),'-','')) WHERE pid       IN (1,4,8,17,18,22,25,26,30,34,35,40,41) AND uuid IS NULL;

-- forms registry (one row per encounter so the encounter shows in the chart timeline)
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, groupname, authorized, deleted, formdir)
SELECT fe.date, fe.encounter, 'New Patient Encounter', fe.id, fe.pid, 'admin', 'Default', 1, 0, 'newpatient'
FROM form_encounter fe
WHERE fe.pid IN (1,4,8,17,18,22,25,26,30,34,35,40,41)
  AND fe.encounter BETWEEN 2000 AND 2999;

-- Sanity-check
SELECT pd.pid, pd.fname, pd.lname,
       (SELECT COUNT(*) FROM prescriptions p WHERE p.patient_id = pd.pid AND p.active = 1) AS meds,
       (SELECT COUNT(*) FROM lists l WHERE l.pid = pd.pid AND l.type = 'medical_problem' AND l.activity = 1) AS problems,
       (SELECT COUNT(*) FROM lists l WHERE l.pid = pd.pid AND l.type = 'allergy' AND l.activity = 1) AS allergies,
       (SELECT COUNT(*) FROM form_encounter fe WHERE fe.pid = pd.pid) AS encounters
FROM patient_data pd
WHERE pd.pid IN (1,4,5,8,17,18,22,25,26,30,34,35,40,41)
ORDER BY pd.pid;
