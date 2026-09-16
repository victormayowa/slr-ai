// Load test for OmniReview's everyday reads, with pass/fail thresholds. Run against staging, never production data you
// can't afford to slow down:
//
//   k6 run -e BASE_URL=https://staging.example.org -e EMAIL=loadtest@example.org -e PASSWORD=... ops/k6/smoke-load.js
//
// The account should belong to a project with realistic data. AI and analysis jobs are not exercised here: they run in
// the worker queue, so test them separately by watching queue depth while submitting batches.
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '1m', target: 20 },
    { duration: '5m', target: 50 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800', 'p(99)<2000'],
  },
};

const BASE = __ENV.BASE_URL;

export function setup() {
  const response = http.post(`${BASE}/api/auth/login`, JSON.stringify({ identifier: __ENV.EMAIL, password: __ENV.PASSWORD }), {
    headers: { 'Content-Type': 'application/json' },
  });
  check(response, { 'signed in': r => r.status === 200 });
  const token = response.json('access_token');
  const projects = http.get(`${BASE}/api/projects`, { headers: { Authorization: `Bearer ${token}` } }).json();
  return { token, projectId: projects.length ? projects[0].id : null };
}

export default function (data) {
  const headers = { headers: { Authorization: `Bearer ${data.token}` } };
  check(http.get(`${BASE}/api/projects`, headers), { projects: r => r.status === 200 });
  if (data.projectId) {
    const base = `${BASE}/api/projects/${data.projectId}`;
    check(http.get(`${base}/workflow`, headers), { workflow: r => r.status === 200 });
    check(http.get(`${base}/records`, headers), { records: r => r.status === 200 });
    check(http.get(`${base}/screening/queue?limit=25`, headers), { queue: r => r.status === 200 });
    check(http.get(`${base}/prisma`, headers), { prisma: r => r.status === 200 });
  }
  check(http.get(`${BASE}/api/notifications`, headers), { notifications: r => r.status === 200 });
  sleep(1);
}
