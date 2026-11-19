#!/usr/bin/env bash
# One command for Track C: data -> serving-like re-ingest (Track B) -> train -> evaluate -> tests.
# Usage (from ml/):  bash run_track_c.sh [stub|real]   (real = BGE-small embeddings; needs the cached model)
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"
EMB="${1:-stub}"
python3 data_rank/make_corpus.py --seed 7
python3 data_rank/reingest_corpus.py --embedder "$EMB"
python3 -m docint.rank.train
python3 -m docint.eval.run_eval --embedder "$EMB"
python3 -m docint.eval.run_eval --embedder "$EMB" --corpus data_rank/corpus_ingested.jsonl --out data_rank/results/serving_like
python3 -m docint.rank.fresh_test
python3 -m docint.rank.study_report
python3 -m pytest tests -q
