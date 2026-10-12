.PHONY: help setup test corpus tier-c eval keyterm sweep report all clean cache-stats

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## create the venv and install
	uv venv --python 3.12
	uv pip install -e . && uv pip install pytest

test:  ## run tests -- no API key needed
	.venv/bin/python -m pytest tests/ -q

corpus:  ## build the evaluation corpus (no API key needed)
	.venv/bin/python scripts/01_build_corpus.py --tiers ABCD

tier-c:  ## generate the tier C recording script + label files
	.venv/bin/python scripts/make_tier_c_script.py

eval:  ## baseline transcription + scoring
	.venv/bin/python scripts/02_run_eval.py --arms baseline

keyterm:  ## all three keyterm arms (lift AND false-trigger analysis)
	.venv/bin/python scripts/02_run_eval.py --arms baseline keyterm_all keyterm_half

sweep:  ## endpointing latency/truncation sweep (streaming, real time)
	.venv/bin/python scripts/04_endpointing_sweep.py --n 20

report:  ## render the report and apply the launch gate (non-zero on blocker)
	.venv/bin/python scripts/05_report.py

all: corpus keyterm sweep report  ## the full run

cache-stats:  ## how many API responses are cached
	@.venv/bin/python -c "from dgeval import cache; import sys; sys.path.insert(0,'src'); print(cache.stats())"

clean:  ## remove generated audio and results, KEEP the response cache
	rm -rf data/corpus/audio/* data/corpus/manifest.jsonl results/* reports/*.md
	@echo "kept .cache/ -- delete it manually to force paid re-runs"
