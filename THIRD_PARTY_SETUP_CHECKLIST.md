# Third-Party Setup Checklist

This checklist reflects the current backend codepaths as of April 12, 2026.

## 1. Boot prerequisites

- Install dependencies in the same interpreter that runs the API: `pip install -r requirements.txt`
- Verify `itsdangerous` is installed, because `SessionMiddleware` requires it.
- Ensure PostgreSQL has `postgis` and `uuid-ossp` enabled.
- Ensure Redis is reachable for auth revocation, slot locks, rate limiting, and Celery.

## 2. Database migration

- Migration infrastructure is now present via [`alembic.ini`](/d:/Strtup1/pandit-booking/alembic.ini) and [`migrations/env.py`](/d:/Strtup1/pandit-booking/migrations/env.py).
- New migration file: [`20260412_01_add_pandit_payout_fields.py`](/d:/Strtup1/pandit-booking/migrations/versions/20260412_01_add_pandit_payout_fields.py)
- Run: `alembic upgrade head`

If this database was previously created without working Alembic source migrations, verify the `alembic_version` table first. You may need to `alembic stamp 20260412_01` after manually confirming schema state.

## 3. Required environment variables for launch

- Core app
  `SECRET_KEY`
  `JWT_SECRET_KEY`
  `DATABASE_URL`
  `REDIS_URL`
- Frontend routing
  `FRONTEND_URL`
  `ADMIN_FRONTEND_URL`
  `ALLOWED_ORIGINS`

Templates updated in:
- [`.env.example`](/d:/Strtup1/pandit-booking/.env.example)
- [`.env.production`](/d:/Strtup1/pandit-booking/.env.production)

## 4. Google OAuth

Required now because auth depends on it.

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `GOOGLE_AUTH_SUCCESS_REDIRECT_URL`
- `GOOGLE_AUTH_FAILURE_REDIRECT_URL`

Checklist:
- Add the backend callback URL in Google Cloud.
- Make sure the redirect URI matches the deployed API domain exactly.
- Make sure the success/failure redirect URLs point to your frontend routes.

## 5. Razorpay

Required now because booking payment flows depend on it.

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`
- `RAZORPAY_ACCOUNT_NUMBER`

Checklist:
- Use test keys first, then live keys.
- Configure webhook endpoint: `/payments/webhook`
- Enable Razorpay X before relying on payout tasks.
- Do not enable automated payouts until pandit bank data collection is fully implemented in the product flow.

## 6. Notifications

Useful but can be phased if needed.

Firebase push:
- `FIREBASE_CREDENTIALS_PATH`
- `FIREBASE_PROJECT_ID`

Twilio SMS:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`

Resend email:
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME`

## 7. Search

Optional for first launch because the app falls back to Postgres search.

- `ELASTICSEARCH_URL`
- `ELASTICSEARCH_USERNAME`
- `ELASTICSEARCH_PASSWORD`
- `ELASTICSEARCH_INDEX_PANDITS`
- `ELASTICSEARCH_INDEX_POOJAS`

If enabled:
- Bootstrap indices after DB data exists.
- Confirm verified pandits are indexed after admin approval.

## 8. Storage

Settings exist, but uploads are not the first blocker for launch.

- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_PUBLIC`
- `S3_BUCKET_PRIVATE`
- `S3_ENDPOINT_URL`
- `S3_REGION`

## 9. Background workers

Needed for refunds, payouts, reminders, and asynchronous notification delivery.

- Run Celery worker with the same env as the API.
- Ensure `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` are valid.

## 10. Smoke-check sequence after credentials are added

1. `GET /health`
2. Google login and callback
3. Create booking
4. Initiate payment
5. Verify payment
6. Pandit accepts booking
7. Pandit completes booking
8. Admin verify/reject/suspend/reinstate flow
9. Razorpay webhook delivery
10. One push/SMS/email notification path
