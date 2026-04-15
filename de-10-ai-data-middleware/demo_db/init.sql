\set ON_ERROR_STOP on

DROP TABLE IF EXISTS allergies CASCADE;
CREATE TABLE allergies (
    start TEXT,
    stop TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT
);
COPY allergies (start, stop, patient, encounter, code, description) FROM '/docker-entrypoint-initdb.d/csv/allergies.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS careplans CASCADE;
CREATE TABLE careplans (
    id TEXT,
    start TEXT,
    stop TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT,
    reasoncode TEXT,
    reasondescription TEXT
);
COPY careplans (id, start, stop, patient, encounter, code, description, reasoncode, reasondescription) FROM '/docker-entrypoint-initdb.d/csv/careplans.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS conditions CASCADE;
CREATE TABLE conditions (
    start TEXT,
    stop TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT
);
COPY conditions (start, stop, patient, encounter, code, description) FROM '/docker-entrypoint-initdb.d/csv/conditions.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS encounters CASCADE;
CREATE TABLE encounters (
    id TEXT,
    start TEXT,
    stop TEXT,
    patient TEXT,
    provider TEXT,
    encounterclass TEXT,
    code TEXT,
    description TEXT,
    cost TEXT,
    reasoncode TEXT,
    reasondescription TEXT
);
COPY encounters (id, start, stop, patient, provider, encounterclass, code, description, cost, reasoncode, reasondescription) FROM '/docker-entrypoint-initdb.d/csv/encounters.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS imaging_studies CASCADE;
CREATE TABLE imaging_studies (
    id TEXT,
    date TEXT,
    patient TEXT,
    encounter TEXT,
    bodysite_code TEXT,
    bodysite_description TEXT,
    modality_code TEXT,
    modality_description TEXT,
    sop_code TEXT,
    sop_description TEXT
);
COPY imaging_studies (id, date, patient, encounter, bodysite_code, bodysite_description, modality_code, modality_description, sop_code, sop_description) FROM '/docker-entrypoint-initdb.d/csv/imaging_studies.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS immunizations CASCADE;
CREATE TABLE immunizations (
    date TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT,
    cost TEXT
);
COPY immunizations (date, patient, encounter, code, description, cost) FROM '/docker-entrypoint-initdb.d/csv/immunizations.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS medications CASCADE;
CREATE TABLE medications (
    start TEXT,
    stop TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT,
    cost TEXT,
    dispenses TEXT,
    totalcost TEXT,
    reasoncode TEXT,
    reasondescription TEXT
);
COPY medications (start, stop, patient, encounter, code, description, cost, dispenses, totalcost, reasoncode, reasondescription) FROM '/docker-entrypoint-initdb.d/csv/medications.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS observations CASCADE;
CREATE TABLE observations (
    date TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT,
    value TEXT,
    units TEXT,
    type TEXT
);
COPY observations (date, patient, encounter, code, description, value, units, type) FROM '/docker-entrypoint-initdb.d/csv/observations.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS organizations CASCADE;
CREATE TABLE organizations (
    id TEXT,
    name TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    phone TEXT,
    utilization TEXT
);
COPY organizations (id, name, address, city, state, zip, phone, utilization) FROM '/docker-entrypoint-initdb.d/csv/organizations.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS patients CASCADE;
CREATE TABLE patients (
    id TEXT,
    birthdate TEXT,
    deathdate TEXT,
    ssn TEXT,
    drivers TEXT,
    passport TEXT,
    prefix TEXT,
    first TEXT,
    last TEXT,
    suffix TEXT,
    maiden TEXT,
    marital TEXT,
    race TEXT,
    ethnicity TEXT,
    gender TEXT,
    birthplace TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT
);
COPY patients (id, birthdate, deathdate, ssn, drivers, passport, prefix, first, last, suffix, maiden, marital, race, ethnicity, gender, birthplace, address, city, state, zip) FROM '/docker-entrypoint-initdb.d/csv/patients.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS procedures CASCADE;
CREATE TABLE procedures (
    date TEXT,
    patient TEXT,
    encounter TEXT,
    code TEXT,
    description TEXT,
    cost TEXT,
    reasoncode TEXT,
    reasondescription TEXT
);
COPY procedures (date, patient, encounter, code, description, cost, reasoncode, reasondescription) FROM '/docker-entrypoint-initdb.d/csv/procedures.csv' WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS providers CASCADE;
CREATE TABLE providers (
    id TEXT,
    organization TEXT,
    name TEXT,
    gender TEXT,
    speciality TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    utilization TEXT
);
COPY providers (id, organization, name, gender, speciality, address, city, state, zip, utilization) FROM '/docker-entrypoint-initdb.d/csv/providers.csv' WITH (FORMAT csv, HEADER true);
