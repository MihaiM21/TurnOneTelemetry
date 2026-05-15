# auth

Reserved for the user accounts / JWT / Stripe billing system that is currently
stubbed in `src/core/config.py` (`jwt_secret_key`, `stripe_*` fields).

Intended layout once implemented:

- `users.py` — User model, registration, password hashing
- `jwt.py` — Token issuance & validation
- `billing.py` — Stripe subscription state, webhook handler
- routes wired into `src/api/routers/auth.py` and `src/api/routers/billing.py`

Backed by a `users` collection in MongoDB.
