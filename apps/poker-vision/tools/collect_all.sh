#!/usr/bin/env bash
# Reproducible dataset plan. Usage: collect_all.sh <out_dir> [python] [seed_offset] [session_regex]
OUT=${1:?out dir}; PY=${2:-python}; OFF=${3:-0}   # OFF shifts every seed: a different OFF gives a fresh, disjoint set of deals
C="$PY $(dirname "$0")/collect_poker_train.py --out $OUT"
jobs_list=(
 # in-distribution (WSOP cash theme, 520x900@1x): disjoint sessions -> train / test split by session
 "--session A_tr1 --fmt NL10 --hands 12 --seed $((111+OFF))" "--session A_tr2 --fmt NL2 --hands 12 --seed $((112+OFF))"
 "--session A_tr3 --fmt NL5 --hands 12 --seed $((113+OFF))" "--session A_tr4 --fmt NL25 --hands 12 --seed $((114+OFF))"
 "--session A_tr5 --fmt NL10 --hands 12 --seed $((115+OFF))" "--session A_tr6 --fmt NL5 --hands 12 --seed $((116+OFF))"
 "--session A_te1 --fmt NL10 --hands 12 --seed $((121+OFF))" "--session A_te2 --fmt NL25 --hands 12 --seed $((122+OFF))"
 "--session A_te3 --fmt NL2 --hands 12 --seed $((123+OFF))"
 # novel themes (same layout size)
 "--session B_ept --fmt EPT --hands 10 --seed $((31+OFF))" "--session B_wpt --fmt WPT --hands 10 --seed $((32+OFF))"
 "--session B_daily --fmt WSOPDaily --hands 10 --seed $((33+OFF))" "--session B_main --fmt WSOPMain --hands 10 --seed $((34+OFF))"
 "--session B_turbo --fmt Turbo --hands 10 --seed $((35+OFF))" "--session B_k100 --fmt K100 --hands 10 --seed $((36+OFF))"
 # novel layouts / scale (WSOP theme and a novel theme)
 "--session C_phone3x --fmt NL10 --hands 8 --seed $((41+OFF)) --vw 390 --vh 844 --dpr 3"
 "--session C_dpr2 --fmt NL10 --hands 8 --seed $((42+OFF)) --dpr 2"
 "--session C_desk --fmt NL10 --hands 8 --seed $((43+OFF)) --vw 1280 --vh 800"
 "--session C_tab --fmt NL10 --hands 8 --seed $((44+OFF)) --vw 768 --vh 1024 --dpr 1.5"
 "--session C_ept_phone --fmt EPT --hands 8 --seed $((45+OFF)) --vw 390 --vh 844 --dpr 2"
 "--session C_ept_desk --fmt EPT --hands 8 --seed $((46+OFF)) --vw 1280 --vh 800 --dpr 1.25"
)
eval_jobs() { for j in "${jobs_list[@]}"; do eval "echo \"$j\""; done; }
eval_jobs | grep -E "${4:-.}" | xargs -P 4 -I{} sh -c "$C {}"
