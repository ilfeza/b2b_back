-- Local development credentials; use provisioned roles and secrets in production.
CREATE ROLE b2b LOGIN PASSWORD 'b2b';
CREATE ROLE goods LOGIN PASSWORD 'goods';
CREATE ROLE publications LOGIN PASSWORD 'publications';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA b2b AUTHORIZATION b2b;
CREATE SCHEMA goods AUTHORIZATION goods;
CREATE SCHEMA publications AUTHORIZATION publications;
GRANT CONNECT ON DATABASE marketplace TO b2b, goods, publications;
