-- The schema create_all made before migrations: sqlite_master's SQL after develop 0a8756e's
-- init_db() on an empty file, with trailing spaces removed. Do not regenerate it from the
-- current models: it stands for databases created before migrations.
CREATE TABLE items (
	id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	keywords TEXT,
	target_price FLOAT,
	notify_email VARCHAR(255) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX ix_items_name ON items (name);
CREATE TABLE price_records (
	id INTEGER NOT NULL,
	item_id INTEGER NOT NULL,
	price FLOAT NOT NULL,
	currency VARCHAR(10) NOT NULL,
	source VARCHAR(255) NOT NULL,
	url TEXT,
	title TEXT,
	checked_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(item_id) REFERENCES items (id)
);
CREATE INDEX ix_price_records_item_id ON price_records (item_id);
