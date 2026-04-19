## Production Launch Guide

This guide is tailored to the current codebase and the environment variables in `.env.production`.

### 1. Prepare production domains

Decide the final public URLs before configuring third-party dashboards.

- Backend API: `https://api.yourdomain.com`
- User frontend: `https://app.yourdomain.com`
- Admin frontend: `https://admin.yourdomain.com`

Update these values in `.env.production`:

- `FRONTEND_URL`
- `ADMIN_FRONTEND_URL`
- `ALLOWED_ORIGINS`
- `GOOGLE_REDIRECT_URI`
- `GOOGLE_AUTH_SUCCESS_REDIRECT_URL`
- `GOOGLE_AUTH_FAILURE_REDIRECT_URL`

### 2. Decide how production will load env vars

The app now supports `ENV_FILE`, but the safest production approach is still real environment variables from your host, container platform, or secret manager.

Options:

1. Preferred: inject values as environment variables in your deployment platform.
2. Acceptable: keep a private `.env.production` on the server and set `ENV_FILE=.env.production`.

Do not commit real secrets into git.

### 3. Fill the required core values first

Set these before starting the app:

- `APP_ENV=production`
- `DEBUG=false`
- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

Recommended checks:

- Postgres is reachable from the API container/host.
- Redis is reachable from both API and Celery worker.
- SSL is enabled if your database provider requires it.

### 4. Configure Google OAuth

Dashboard actions:

1. Open Google Cloud Console.
2. Create or open the OAuth client.
3. Add authorized redirect URI:
   `https://api.yourdomain.com/auth/google/callback`
4. Add authorized origins for your frontend if required by your frontend flow.

Map credentials to:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`

Frontend behavior:

- On success the backend can now redirect to `GOOGLE_AUTH_SUCCESS_REDIRECT_URL` with query params:
  `access_token`, `token_type`, `expires_in`
- On failure it can redirect to `GOOGLE_AUTH_FAILURE_REDIRECT_URL?error=...`

### 5. Configure Razorpay live payments

Dashboard actions:

1. Switch Razorpay to live mode.
2. Generate live `Key ID` and `Key Secret`.
3. Create a webhook secret.
4. Add webhook URL:
   `https://api.yourdomain.com/payments/webhook`
5. Subscribe at least to:
   `payment.captured`, `payment.failed`, `refund.processed`

Map credentials to:

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`

If you want automated payouts to pandits:

1. Enable Razorpay X.
2. Get your Razorpay X account number.
3. Set `RAZORPAY_ACCOUNT_NUMBER`.

### 6. Configure Firebase push notifications

Dashboard actions:

1. Create or open the Firebase project.
2. Enable Cloud Messaging.
3. Generate a service account key JSON.
4. Store that JSON securely on the server.

Map values to:

- `FIREBASE_PROJECT_ID`
- `FIREBASE_CREDENTIALS_PATH`

Recommended server path:

- Linux: `/etc/secrets/firebase-credentials.json`

### 7. Configure Twilio SMS

Dashboard actions:

1. Collect `Account SID`.
2. Collect `Auth Token`.
3. Buy or verify a sender number.
4. If production SMS is needed in India, confirm DLT and template compliance on your Twilio setup.

Map values to:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`

### 8. Configure Resend email

Dashboard actions:

1. Verify your sending domain.
2. Add DNS records required by Resend.
3. Generate an API key.

Map values to:

- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME`

Example:

- `EMAIL_FROM=noreply@yourdomain.com`

### 9. Configure Elasticsearch if you want search acceleration

This app can fall back to Postgres search logic, so Elasticsearch is optional for first launch but recommended for scale.

Map values to:

- `ELASTICSEARCH_URL`
- `ELASTICSEARCH_USERNAME`
- `ELASTICSEARCH_PASSWORD`
- `ELASTICSEARCH_INDEX_PANDITS`
- `ELASTICSEARCH_INDEX_POOJAS`

After deploying, ensure the pandit index exists by exercising pandit profile sync flows or by running your index creation utility if you use one operationally.

### 10. Treat S3/R2 as a later launch item unless uploads are already wired

The settings exist, but storage integration is not fully wired across the codebase yet.

Only fill these now if you are also implementing uploads:

- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_BUCKET_PUBLIC`
- `S3_BUCKET_PRIVATE`
- `S3_ENDPOINT_URL`
- `S3_REGION`

### 11. Start required runtime services

Minimum production services:

1. API
2. Postgres
3. Redis
4. Celery worker

Optional:

1. Elasticsearch
2. Flower
3. Metrics collector / OTEL backend

Important:

- Background notifications and payout tasks depend on Celery.
- Launch is incomplete if the API is running but the worker is not.

### 12. Run the go-live verification sequence

Execute these checks in order:

1. Health check
   - `GET /health`
2. Google login
   - Complete one real sign-in from the frontend
3. Payment
   - Create one low-value live Razorpay payment
4. Webhook
   - Confirm the payment webhook reaches the backend
5. Notifications
   - Send one push notification
   - Send one SMS
   - Send one email
6. Background jobs
   - Confirm Celery worker is processing tasks
7. Search
   - Search for a verified pandit with a real saved location

### 13. Final security checks

- Keep `DEBUG=false`
- Use strong unique `SECRET_KEY`
- Use strong unique `JWT_SECRET_KEY`
- Restrict `ALLOWED_ORIGINS` to real frontend domains only
- Use HTTPS for every public domain
- Keep secrets outside version control
- Store Firebase JSON outside the repo
- Use managed secrets where possible

### 14. Known implementation notes

- `ENV_FILE` is now supported, so the app is no longer hard-wired to `.env` only.
- Google OAuth callback now supports redirecting back to the frontend after login.
- Razorpay payout automation now has a matching `RAZORPAY_ACCOUNT_NUMBER` setting.
- Search indexing no longer writes a hardcoded placeholder coordinate when pandit coordinates are available.
