# One-time setup on a fresh clone: python venv (client tests, E2E simulator, stack tests), node packages, the Playwright browser.
.PHONY: pepper setup up test-api test-web e2e stack-tests
# Random SERVER_PEPPER in .env (git-ignored; docker compose reads it). Created once, never overwritten:
# changing it later invalidates every key and session.
pepper:
	@if grep -qs '^SERVER_PEPPER=' .env; then echo ".env already has SERVER_PEPPER (kept)"; else printf 'SERVER_PEPPER=%s\n' "$$(python3 -c 'import secrets;print(secrets.token_hex(32))')" >> .env; echo "wrote SERVER_PEPPER to .env"; fi

setup: pepper
	python3 -m venv .venv
	.venv/bin/pip install -q httpx pytest ./client
	cd web && npm ci
	cd web && npx playwright install chromium

up:
	docker compose up --build -d postgres api

test-web:
	mkdir -p results
	cd web && npm run typecheck && npx vitest run 2>&1 | tee ../results/web-unit-tests-$$(date +%Y%m%d-%H%M%S).txt

test-api:
	mkdir -p results
	docker compose up -d --build postgres
	docker compose build -q api
	docker compose run --rm -T api pytest -v 2>&1 | tee results/api-tests-$$(date +%Y%m%d-%H%M%S).txt

# E2E: needs the .venv (python3 -m venv .venv && .venv/bin/pip install httpx) and `npx playwright install chromium`.
# Rate limits are raised because every client shares the chaos proxy's IP; fault hooks stay OFF. Settings are recorded.
e2e:
	mkdir -p results/e2e
	RL_PROJECTS_PER_MIN=100000 RL_SESSION_PER_MIN=100000 RL_AUTH_FAIL_PER_MIN=100000 RL_INGEST_PER_S=100000 FAULT_HOOKS=0 VITE_TEST_HOOKS=1 \
	  docker compose up -d --build
	printf 'RL_PROJECTS_PER_MIN=100000 RL_SESSION_PER_MIN=100000 RL_AUTH_FAIL_PER_MIN=100000 RL_INGEST_PER_S=100000 FAULT_HOOKS=0 VITE_TEST_HOOKS=1 TRUSTED_PROXY_HOPS=1 RESYNC_THRESHOLD=10000\n' > results/e2e/settings.txt
	cd web && npx playwright test 2>&1 | tee ../results/e2e/run-$$(date +%Y%m%d-%H%M%S).txt

# Stack tests: real HTTP against the running compose stack (nginx gateway + chaos proxy). Rate limits raised (one shared IP).
stack-tests:
	mkdir -p results/stack
	RL_PROJECTS_PER_MIN=100000 RL_SESSION_PER_MIN=100000 RL_AUTH_FAIL_PER_MIN=100000 RL_INGEST_PER_S=100000 FAULT_HOOKS=0 \
	  docker compose up -d --build
	sleep 6
	printf 'RL_PROJECTS_PER_MIN=100000 RL_SESSION_PER_MIN=100000 RL_AUTH_FAIL_PER_MIN=100000 RL_INGEST_PER_S=100000 FAULT_HOOKS=0 TRUSTED_PROXY_HOPS=1 RESYNC_THRESHOLD=10000\n' > results/stack/settings.txt
	.venv/bin/python -m pytest tests/stack -v -p no:cacheprovider 2>&1 | tee results/stack/run-$$(date +%Y%m%d-%H%M%S).txt
