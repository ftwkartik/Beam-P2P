# Beam

Send files directly between devices — browser to browser, or terminal to browser —
over an encrypted peer-to-peer connection. No account required, no size limit but your
disk, and every block is verified.

This is a from-scratch, production-quality peer-to-peer file-transfer app. See
`docs/roadmap.md` for the build plan.

> **Status:** in progress (Milestone 1 of 12 — see `docs/roadmap.md`). This README is
> a working stub; the full version is written in Milestone 12.

## Quick start

```bash
cp .env.example .env
./scripts/gen_secrets.sh   # fills in JWT_SECRET, CODE_PEPPER, TURN_SECRET locally
docker compose up --build
```

- Web client: <http://localhost:8080>
- API: <http://localhost:8000> (`/health`, `/ready`)

No API keys or paid services are required anywhere in this project.

## Documentation

| Doc | Contents |
|---|---|
| [docs/product.md](docs/product.md) | Product scope, user journey, feature tiers |
| [docs/architecture.md](docs/architecture.md) | System design, key flows, engineering Q&A |
| [docs/protocol.md](docs/protocol.md) | The signaling and peer wire protocols |
| [docs/data-model.md](docs/data-model.md) | Redis keyspace and the deferred Postgres schema |
| [docs/security.md](docs/security.md) | Threat model, SSRF defenses, MITM detection |
| [docs/testing-strategy.md](docs/testing-strategy.md) | Test pyramid and benchmarks |
| [docs/roadmap.md](docs/roadmap.md) | Milestones 1–12 |
| [docs/adr/](docs/adr/) | Architecture decision records |

## Development

```bash
# Python (protocol, server, cli packages)
uv sync --all-packages
uv run ruff check .
uv run mypy protocol/src server/src cli/src
uv run pytest

# Web client
cd web && npm ci
npm run lint && npm run typecheck && npm test
```

`pre-commit install` runs formatting, linting and secret-scanning hooks on commit.
