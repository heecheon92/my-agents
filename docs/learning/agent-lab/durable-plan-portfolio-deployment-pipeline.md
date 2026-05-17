---
created: 2026-05-15
updated: 2026-05-15
status: active
topics:
  - deployment
  - aws
  - lightsail
  - github-actions
  - docker
  - portfolio
related_code:
  - pyproject.toml
  - main.py
  - my_agents/api.py
---

# Durable plan: portfolio deployment pipeline

## Decision

Use **AWS Lightsail + Docker Compose + GitHub Actions** as the first deployment pipeline for this backend.

This is the durable plan until the project needs real multi-user production operations.

```text
GitHub repository
  -> GitHub Actions CI
      -> tests, lint, format checks
      -> Docker build
      -> SSH deploy to AWS Lightsail
          -> Docker Compose
              -> FastAPI app
              -> Caddy or Nginx HTTPS reverse proxy
              -> /health smoke check
```

## Why this path

This project is currently a backend-only FastAPI + LangGraph assistant foundation. It needs a portfolio-grade deployment that is real enough to show engineering judgment, but not so complex that deployment work overwhelms the learning roadmap.

Lightsail + Docker Compose is the right first deployment target because it is:

- simple enough to operate alone;
- product-shaped enough to demonstrate Docker, Linux, env vars, HTTPS, health checks, and CI/CD;
- cheaper and more predictable than prematurely assembling several AWS services;
- easier to explain in a portfolio than a hidden platform deployment;
- migratable later to ECS/Fargate when the app has users, auth, data, and usage pressure.

## AWS Free Tier posture

AWS Free Tier is acceptable for a low-traffic portfolio demo, but it should not be treated as the product architecture.

The project should assume:

- free tier credits/trials are temporary;
- billing alarms are mandatory;
- public OpenAI-backed endpoints can create real cost;
- predictable low monthly cost is better than a fragile “must be free” design.

## Portfolio deployment scope

The first deployed version should expose only safe surfaces:

- public `/health` endpoint;
- public or protected FastAPI docs depending on comfort level;
- deterministic demo chat endpoint, or OpenAI-backed chat only behind protection;
- no public unauthenticated OpenAI spending path;
- no frontend in this repository.

Recommended public demo posture:

```text
Unauthenticated user
  -> /health
  -> optional deterministic /assistant/chat demo

Owner/admin
  -> protected OpenAI-backed chat
```

## Phase 1: portfolio demo

Implement deployment with:

1. `Dockerfile` for the FastAPI app.
2. `docker-compose.yml` for app + reverse proxy.
3. Caddy or Nginx for HTTPS and reverse proxy.
4. GitHub Actions workflow:
   - `uv run pytest -q`
   - `uv run ruff check . --no-cache`
   - `uv run ruff format --check .`
   - build container image
   - SSH deploy to Lightsail
   - run `/health` smoke check
5. AWS billing budget/alarm before leaving anything running.
6. `.env.example` updates for deployment-safe settings.
7. README deployment section in both Korean and English.

## Phase 2: product-readiness upgrades

Before serving real users, add:

- authentication;
- user/workspace data model;
- database;
- rate limits;
- OpenAI usage tracking;
- request logging without secrets;
- secret management;
- backup/restore path;
- basic abuse controls;
- separate dev/staging/prod configuration.

At this point, public OpenAI-backed chat can be considered only with auth, per-user quotas, and cost monitoring.

## Phase 3: product-grade AWS migration

When the product needs more reliable operations, migrate toward:

```text
GitHub Actions
  -> ECR
  -> ECS Fargate or ECS Express Mode
  -> ALB + ACM + Route 53
  -> RDS/Postgres
  -> Secrets Manager
  -> CloudWatch
```

This phase is intentionally deferred. Starting here would be overengineering for the current learning and portfolio stage.

## Avoid for now

Do not start with:

- API Gateway + Lambda + RDS + Cognito + VPC as the first deployment path;
- Kubernetes;
- a frontend in this repository;
- public unauthenticated OpenAI-backed chat;
- AWS App Runner for new deployment planning, because AWS announced it would stop accepting new customers starting April 30, 2026.

## Stop condition for this plan

Stay on Lightsail + Docker Compose until at least one of these becomes true:

- multiple real users need isolated data;
- auth and database are implemented;
- uptime, scaling, audit, or observability needs exceed a single VPS-style deployment;
- deployment operations become more complex than ECS/Fargate would be;
- cost or security requirements demand managed AWS primitives.

## Decision summary

| Stage | Deployment choice | Reason |
| --- | --- | --- |
| Now / portfolio | Lightsail + Docker Compose + GitHub Actions | Low complexity, real deployment skills, predictable cost |
| Early private use | Same, plus auth/rate limits/logging | Still simple, safer OpenAI exposure |
| Product stage | ECS/Fargate, ALB, RDS, Secrets Manager, CloudWatch | Better isolation, scale, operations |

## References

- AWS Free Tier documentation: <https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html>
- EC2 Free Tier usage documentation: <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html>
- Lightsail pricing and Free Tier information: <https://aws.amazon.com/lightsail/pricing/>
- AWS App Runner end-of-support notice for new customers: <https://aws.amazon.com/apprunner/>

## Revision history

- 2026-05-15: Created learning log for `Durable plan: portfolio deployment pipeline`.
