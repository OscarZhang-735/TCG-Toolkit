CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL,
    password_hash TEXT NOT NULL, created_at BIGINT NOT NULL,
    email TEXT UNIQUE, email_verified_at BIGINT
);
CREATE UNIQUE INDEX users_username ON users (lower(username));
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    expires_at BIGINT NOT NULL
);
CREATE INDEX sessions_expiry ON sessions(expires_at);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE favorites (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    game TEXT NOT NULL, product_url TEXT NOT NULL, snapshot TEXT NOT NULL,
    created_at BIGINT NOT NULL, refreshed_at BIGINT,
    sort_id BIGINT GENERATED ALWAYS AS IDENTITY,
    UNIQUE(user_id, game, product_url)
);
CREATE TABLE owned_cards (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    game TEXT NOT NULL, card_number TEXT NOT NULL, rarity TEXT NOT NULL,
    name TEXT NOT NULL, quantity BIGINT NOT NULL CHECK(quantity >= 0),
    notes TEXT NOT NULL, game_key TEXT NOT NULL, number_key TEXT NOT NULL,
    rarity_key TEXT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
    image_url TEXT NOT NULL DEFAULT '', sort_id BIGINT GENERATED ALWAYS AS IDENTITY,
    UNIQUE(user_id, game_key, number_key, rarity_key)
);
CREATE TABLE cart_operations (
    user_id TEXT NOT NULL REFERENCES users(id), operation_key TEXT NOT NULL,
    request_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('processing','complete')),
    status_code INTEGER, response_json TEXT, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
    PRIMARY KEY(user_id, operation_key)
);
CREATE TABLE email_challenges (
    id TEXT PRIMARY KEY, email TEXT NOT NULL, purpose TEXT NOT NULL,
    user_id TEXT REFERENCES users(id), code_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL CHECK(state IN ('pending','active','failed','superseded','used'))
);
CREATE INDEX email_challenges_quota ON email_challenges(email, created_at);
CREATE INDEX email_challenges_user ON email_challenges(user_id);
CREATE TABLE rates (
    currency TEXT PRIMARY KEY, rate DOUBLE PRECISION,
    fetched_at DOUBLE PRECISION, attempted_at DOUBLE PRECISION
);
