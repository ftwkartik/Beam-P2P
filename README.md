# Beam

Send files directly between devices — browser to browser, or terminal to browser —
over an encrypted peer-to-peer connection. No size limit but your disk, every block
verified, and an optional account if you want a history of what you've sent.

This is a from-scratch, production-quality peer-to-peer file-transfer app. See
`docs/roadmap.md` for the full build plan and `docs/project-report.md` for how each
milestone was verified.

## Quick start

```bash
cp .env.example .env
./scripts/gen_secrets.sh   # fills in JWT_SECRET, CODE_PEPPER, TURN_SECRET locally
docker compose up --build
```

- Web client: <http://localhost:8080>
- API: <http://localhost:8000> (`/health`, `/ready`)

No API keys or paid services are required anywhere in this project. Accounts +
transfer history (see below) run on the bundled Postgres by default; unset
`DATABASE_URL` to disable that surface entirely and run P2P transfer only.

### Using the CLI

```bash
uv run --package beam-cli beam send /path/to/file --server http://localhost:8080
# prints a room code; on the other device:
uv run --package beam-cli beam receive <code> --dir ~/Downloads --server http://localhost:8080
```

Both sides display a short phrase (the SAS) derived from the connection's own
cryptographic fingerprints — read it aloud and confirm it matches before trusting the
transfer; a mismatch means something is intercepting the connection. The CLI
interoperates with the browser client in both directions (`web/e2e/interop.spec.ts`).

### Accounts and transfer history

Entirely optional and separate from the P2P transfer itself, which never needs an
account. If you're signed in, a client can record a completed transfer's metadata
(filenames, sizes, hashes — never file contents, which the server never sees) against
your account for later reference:

```bash
curl -X POST http://localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"..."}'
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"..."}'
# -> {"access_token": "...", "refresh_token": "..."}
curl http://localhost:8080/api/v1/transfers -H 'Authorization: Bearer <access_token>'
```

See `docs/adr/010-accounts-and-history.md` for the design and `docs/data-model.md` §3
for the schema. There's no web UI for this yet (documented as a deliberate scope cut,
not a silent gap) — the API is real and tested, including cross-user isolation,
against a real Postgres.

## Documentation

| Doc | Contents |
|---|---|
| [docs/product.md](docs/product.md) | Product scope, user journey, feature tiers |
| [docs/architecture.md](docs/architecture.md) | System design, key flows, engineering Q&A |
| [docs/protocol.md](docs/protocol.md) | The signaling and peer wire protocols |
| [docs/data-model.md](docs/data-model.md) | Redis keyspace and the accounts/history Postgres schema |
| [docs/security.md](docs/security.md) | Threat model, SSRF defenses, MITM detection |
| [docs/testing-strategy.md](docs/testing-strategy.md) | Test pyramid and benchmarks |
| [docs/roadmap.md](docs/roadmap.md) | Milestones 1–12 |
| [docs/project-report.md](docs/project-report.md) | What was built, and how each part was verified |
| [docs/adr/](docs/adr/) | Architecture decision records (10, 001–010) |

## Development

```bash
# Python (protocol, server, cli packages)
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run mypy protocol/src server/src cli/src
uv run pytest                              # set TEST_DATABASE_URL / TEST_REDIS_URL
                                            # to run the real-service integration tests

# Web client
cd web && npm ci
npm run lint && npm run typecheck && npm test
npx playwright test                        # full E2E suite against docker compose
                                            # (happy path, resume, TURN relay, CLI<->browser interop)
```

`pre-commit install` runs formatting, linting and secret-scanning hooks on commit.

## Status

All 12 milestones in `docs/roadmap.md` are built: Redis-backed signaling, multi-instance
deployment, TURN, the React web client, the Python CLI, CLI↔browser interop, and the
optional accounts/history API. See `docs/project-report.md` for the verification pass
behind that claim.
