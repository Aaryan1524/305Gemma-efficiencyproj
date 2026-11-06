PYTHON ?= ./venv/bin/python

.PHONY: dev lint test check demo build preflight

dev:
	python3.12 -m venv venv
	$(PYTHON) -m pip install -r requirements-dev.txt
lint:
	$(PYTHON) -m ruff check .
	bash -n start.sh build_app.sh build_dictate.sh
	$(PYTHON) scripts/check_links.py
test:
	$(PYTHON) -m unittest discover -s tests -v
check: lint test
demo:
	$(PYTHON) report.py ledger.sample.jsonl --out /tmp/focusledger-demo.html --demo --offline
build:
	./build_app.sh
	./build_dictate.sh
preflight:
	$(PYTHON) verify_setup.py
