-- Payments for carts. See docs/tech-spec.md §7.1.

CREATE TABLE payments (
    id                  UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id             UUID           NOT NULL REFERENCES carts(id),
    user_id             UUID           NOT NULL REFERENCES users(id),
    payment_method_id   UUID           NOT NULL REFERENCES user_payment_methods(id),
    amount              NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency            CHAR(3)        NOT NULL,
    status              TEXT           NOT NULL DEFAULT 'pending'
                                       CHECK (status IN ('pending', 'succeeded', 'failed')),
    idempotency_key     TEXT           NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    request_fingerprint TEXT           NOT NULL,
    provider_payment_id TEXT           UNIQUE,
    failure_code        TEXT,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_payments_user_idempotency_key UNIQUE (user_id, idempotency_key),
    CONSTRAINT chk_payments_succeeded_has_provider_id
        CHECK (status <> 'succeeded' OR provider_payment_id IS NOT NULL),
    CONSTRAINT chk_payments_failed_has_code
        CHECK (status <> 'failed' OR failure_code IS NOT NULL)
);

CREATE INDEX idx_payments_cart_id ON payments(cart_id);

-- At most one live payment per cart. This is the DB-level backstop against double charges.
CREATE UNIQUE INDEX uq_payments_cart_live
    ON payments(cart_id)
    WHERE status IN ('pending', 'succeeded');

-- Supports the reconciliation of stuck pending payments.
CREATE INDEX idx_payments_pending_created_at
    ON payments(created_at)
    WHERE status = 'pending';
