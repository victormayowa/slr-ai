# Public API

## Documentation

The REST API is described by OpenAPI at `/openapi.json`, with interactive documentation at `/docs` on the API server.
Every screen in the app uses the same API.

## Personal access tokens

Create tokens under **Settings → API tokens**. A token starts with `omr_` and is shown once; only its hash is stored.
Send it as a bearer token:

```
curl -H "Authorization: Bearer omr_..." https://api.example.org/api/projects
```

Tokens have scopes: `read` allows GET requests and `write` allows changes. A token can be limited to certain projects,
and can expire. Tokens act with your role in each project, can't create other tokens, and can't reach administration
endpoints. Revoke a token at any time.

## Webhooks

Project owners, lead reviewers, and methodologists add webhooks on **Data Exchange & API**. A webhook receives a POST
for each audit event matching its patterns, for example `stage.completed`, `stage.reopened`, `manuscript.*`, or `*`.
The JSON body holds the event's id, action, entity, details, time, and audit hash.

Each request carries `X-OmniReview-Event` (the action), `X-OmniReview-Delivery` (the delivery id), and
`X-OmniReview-Signature: sha256=<hex>`, the HMAC-SHA256 of the raw body with the webhook's secret. Verify it before
trusting the payload. Deliveries that fail are retried with increasing delays, up to six attempts.

## FHIR

`GET /api/projects/{id}/fhir/bundle` returns a FHIR R5 collection Bundle in the Evidence-Based Medicine on FHIR style:
Citation resources for the review and its included studies, EvidenceVariable resources for the question's population,
intervention, comparator, and outcomes, and Evidence resources for each GRADE outcome with its statistic, confidence
interval, sample size, and certainty ratings.
