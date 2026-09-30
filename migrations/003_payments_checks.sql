-- Database backstops for rules the service also enforces (NFR-4, FR-12).

ALTER TABLE payments
    -- NFR-4: only fixed codes are stored, never raw provider text.
    ADD CONSTRAINT chk_payments_failure_code_known
        CHECK (failure_code IN ('card_declined', 'provider_error')),
    -- FR-12: only a failed payment carries a failure code.
    ADD CONSTRAINT chk_payments_only_failed_has_code
        CHECK (status = 'failed' OR failure_code IS NULL);
