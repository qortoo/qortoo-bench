PYTHON ?= python3
GO ?= go
SUITE ?=
SOURCE_ROOT ?=

.PHONY: smoke
smoke:
	$(PYTHON) scripts/smoke.py --go "$(GO)" $(if $(SOURCE_ROOT),--source-root "$(SOURCE_ROOT)") $(if $(SUITE),--suite "$(SUITE)")
