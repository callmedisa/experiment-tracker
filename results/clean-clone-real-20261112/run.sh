#!/bin/bash
cd <repo-clone>
step() { echo; echo "######## $(date +%H:%M:%S) $1"; }
step "make setup"; make setup 2>&1 | tail -15
step "quickstart: create project via API (UI step), then the python one-liner from the README"
H=(-H "X-Requested-With: tracker" -H "Origin: http://localhost:8080" -H "Content-Type: application/json")
KEY=$(curl -s "${H[@]}" -X POST http://localhost:8080/api/projects -d '{"name":"quickstart"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["keys"]["ingest"]["key"])')
echo "ingest key obtained: ${KEY:0:11}…"
.venv/bin/python -c "import tracker; r = tracker.start(key='$KEY', run='hello', url='http://localhost:8080/api'); [r.log(s, loss=1/(s+1)) for s in range(50)]; r.finish()" && echo "quickstart python exit 0"
step "make test-api"; make test-api 2>&1 | tail -12
step "make test-web"; make test-web 2>&1 | tail -10
step "client tests"; .venv/bin/python -m pytest client/test_tracker.py 2>&1 | tail -5
step "make e2e"; make e2e 2>&1 | tail -30
step "make stack-tests"; make stack-tests 2>&1 | tail -25
step "done"
