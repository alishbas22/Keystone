-- Brightline Furnishings sales database (KAN-35). Works in PostgreSQL and SQLite.
-- Loaded by `docker compose --profile demo up -d sales-db` (PostgreSQL on port 5433) and built
-- as demo/sales/sales.db (SQLite) by demo/make_sample_docs.py.

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name VARCHAR(80) NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    full_name VARCHAR(80) NOT NULL,
    email VARCHAR(120),
    department_id INTEGER REFERENCES departments(id),
    manager_id INTEGER REFERENCES employees(id)
);

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    vat_number VARCHAR(40),
    industry VARCHAR(60),
    account_manager_id INTEGER REFERENCES employees(id),
    updated_at TIMESTAMP
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    sku VARCHAR(40)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    order_number VARCHAR(40) NOT NULL,
    order_date DATE,
    total NUMERIC(12, 2),
    customer_id INTEGER REFERENCES customers(id)
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    invoice_number VARCHAR(40) NOT NULL,
    order_id INTEGER REFERENCES orders(id)
);

INSERT INTO departments (id, name) VALUES
    (1, 'Sales team'), (2, 'Finance department'), (3, 'Operations team'), (4, 'Retail Partners team');

INSERT INTO employees (id, full_name, email, department_id, manager_id) VALUES
    (1, 'Amina Yusuf', 'amina@brightline.example', NULL, NULL),
    (2, 'Omar Siddiqui', 'omar@brightline.example', 1, 1),
    (3, 'Sara Khan', 'sara.khan@brightline.example', 4, 2),
    (4, 'Bilal Ahmed', 'bilal@brightline.example', 1, 2),
    (5, 'Hina Raza', 'hina@brightline.example', 2, 1),
    (6, 'Usman Tariq', 'usman@brightline.example', 2, 5),
    (7, 'Leila Shah', 'leila@brightline.example', 2, 5);

INSERT INTO customers (id, name, vat_number, industry, account_manager_id, updated_at) VALUES
    (1, 'Harbor Hotels Group', 'GB100200300', 'Hospitality', 4, '2026-09-15 00:00:00'),
    (2, 'Crestview Bank', 'GB200300400', 'Banking', 4, '2026-03-15 00:00:00'),
    (3, 'Greenleaf Clinics', 'GB300400500', 'Healthcare', 4, '2026-04-10 00:00:00'),
    (4, 'Pinecrest Schools', 'GB400500600', 'Education', 3, '2026-06-30 00:00:00');

INSERT INTO products (id, name, sku) VALUES
    (1, 'Ergo Desk Chair', 'BF-CH-100'),
    (2, 'Oak Standing Desk 140 cm', 'BF-DK-220'),
    (3, 'Oak Standing Desk 120 cm', 'BF-DK-210');

INSERT INTO orders (id, order_number, order_date, total, customer_id) VALUES
    (1, 'PO-1042', '2025-08-12', 120000.00, 1),
    (2, 'PO-1107', '2026-02-03', 45000.00, 2),
    (3, 'PO-1188', '2026-08-05', 18500.00, 3),
    (4, 'PO-1203', '2026-06-20', 40000.00, 4);

INSERT INTO order_items (id, order_id, product_id, quantity) VALUES
    (1, 1, 1, 200), (2, 1, 2, 50), (3, 2, 1, 100), (4, 3, 3, 30), (5, 4, 1, 150);

INSERT INTO invoices (id, invoice_number, order_id) VALUES
    (1, 'INV-2025-031', 1), (2, 'INV-2026-044', 2), (3, 'INV-2026-118', 3), (4, 'INV-2026-097', 4);
