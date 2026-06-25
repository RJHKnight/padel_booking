# Cloud Run + Cloud Scheduler Deployment

This runs the padel booker on Google Cloud Run, triggered by Cloud Scheduler.
Cloud Scheduler fires within ~1 second of the target time — far more reliable
than GitHub Actions, which can be delayed 5–30+ minutes.

Session state (your login) is stored in a GCS bucket so the booking run skips
the slow ~40s login and can poll the slot fast the moment it releases.

---

## Prerequisites

- A Google Cloud project with billing enabled
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Set your project:
  ```bash
  export PROJECT_ID="your-project-id"
  export REGION="europe-west2"   # London
  gcloud config set project $PROJECT_ID
  ```

---

## 1. Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com
```

## 2. Create a GCS bucket for the session file

```bash
export BUCKET="${PROJECT_ID}-padel-session"
gcloud storage buckets create gs://$BUCKET --location=$REGION
```

## 3. Build & deploy the container

```bash
gcloud run deploy padel-booker \
  --source . \
  --region $REGION \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 1800 \
  --set-env-vars "HEADLESS=true,SESSION_STATE_PATH=/tmp/flow_session.json,GCS_BUCKET=$BUCKET,TARGET_TIME=19:00,DAYS_AHEAD=5,MAX_RETRIES=30,RETRY_DELAY_S=5" \
  --set-secrets "FLOW_EMAIL=flow-email:latest,FLOW_PASSWORD=flow-password:latest"
```

First create the secrets (one-off):
```bash
echo -n "your.email@example.com" | gcloud secrets create flow-email --data-file=-
echo -n "yourpassword"           | gcloud secrets create flow-password --data-file=-
```

Grant the Cloud Run service account access to the secrets and bucket:
```bash
# Find the service account Cloud Run uses (default compute SA unless customised)
export SA="$(gcloud run services describe padel-booker --region $REGION --format 'value(spec.template.spec.serviceAccountName)')"
# If empty, it's the default compute SA:
export SA="${SA:-$(gcloud projects describe $PROJECT_ID --format 'value(projectNumber)')-compute@developer.gserviceaccount.com}"

gcloud secrets add-iam-policy-binding flow-email \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding flow-password \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member "serviceAccount:$SA" --role roles/storage.objectAdmin
```

## 4. Seed the session (one-off, do this once)

Get the service URL and call /seed with an identity token:
```bash
export URL="$(gcloud run services describe padel-booker --region $REGION --format 'value(status.url)')"
curl -X POST "$URL/seed" -H "Authorization: Bearer $(gcloud auth print-identity-token)"
```
This logs in once and stores the session in your bucket. Re-run it if the
session ever expires (you'll see login happening in the logs again).

## 5. Create the Cloud Scheduler job

Schedule for Thursday 07:59 London time. Cloud Scheduler honours the timezone
including DST, so no need for the dual GMT/BST cron we needed on GitHub.

```bash
# Scheduler needs a service account allowed to invoke the Cloud Run service
gcloud run services add-iam-policy-binding padel-booker \
  --region $REGION \
  --member "serviceAccount:$SA" \
  --role roles/run.invoker

gcloud scheduler jobs create http padel-weekly \
  --location $REGION \
  --schedule "59 7 * * 4" \
  --time-zone "Europe/London" \
  --uri "$URL/book" \
  --http-method POST \
  --oidc-service-account-email "$SA" \
  --oidc-token-audience "$URL" \
  --attempt-deadline 1800s
```

`--schedule "59 7 * * 4"` = 07:59 every Thursday, London time, DST-aware.

---

## Testing

Trigger a run manually any time:
```bash
gcloud scheduler jobs run padel-weekly --location $REGION
# or hit the endpoint directly:
curl -X POST "$URL/book" -H "Authorization: Bearer $(gcloud auth print-identity-token)"
```

View logs:
```bash
gcloud run services logs read padel-booker --region $REGION --limit 100
```

---

## Changing the target time / day

The booking time and lead days are env vars on the service:
```bash
gcloud run services update padel-booker --region $REGION \
  --set-env-vars "TARGET_TIME=20:00,DAYS_AHEAD=5"
```

The schedule (which day it fires) is on the scheduler job:
```bash
gcloud scheduler jobs update http padel-weekly --location $REGION \
  --schedule "59 7 * * 3"   # e.g. Wednesday instead
```

---

## Why this is better than GitHub Actions here

- **Trigger latency**: Cloud Scheduler fires within ~1s; GitHub's shared queue
  can be 5–30 min late, which loses fast-moving slots.
- **DST handled**: one schedule with `Europe/London`, no dual cron.
- **Fast polling**: session reuse means each attempt hits the timetable in
  ~2–3s instead of ~40s, so you can poll hard right at release.
