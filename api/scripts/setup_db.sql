-- Creează utilizatorul și bazele de date pentru 4Health.
-- Poate fi rulat de mai multe ori (sare peste ce există deja).
-- Rulează-l ca utilizatorul "postgres" (parola aleasă la instalarea PostgreSQL):
--   psql -U postgres -h localhost -f api/scripts/setup_db.sql
-- (setup.bat / setup.sh îl rulează automat)

SELECT 'CREATE ROLE health4 WITH LOGIN PASSWORD ''health4'''
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'health4')\gexec

SELECT 'CREATE DATABASE health4 OWNER health4 ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'health4')\gexec

SELECT 'CREATE DATABASE health4_test OWNER health4 ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'health4_test')\gexec
