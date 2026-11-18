#!/usr/bin/env bash
# Ajaa crawl_prospects.py:tä ja käynnistää sen uudelleen, jos heartbeat (valmistuneet yritykset) ei päivity 120 sekuntiin
# (httpx/httpcore-pooli voi lukkiutua pitkissä ajoissa). Tila säilyy, joten uudelleenkäynnistys jatkaa.
# Käyttö: scripts/prospecting/supervise.sh <out-hakemisto> [crawl_prospects.py:n muut argumentit]
OUT="$1"; shift
DIR="$(cd "$(dirname "$0")" && pwd)"
while true; do
  touch "$OUT/heartbeat"
  python3 "$DIR/crawl_prospects.py" --out "$OUT" "$@" >> "$OUT/run.log" 2>&1 &
  PID=$!
  while kill -0 "$PID" 2>/dev/null; do
    sleep 20
    age=$(( $(date +%s) - $(stat -c %Y "$OUT/heartbeat" 2>/dev/null || date +%s) ))
    if [ "$age" -gt 120 ]; then
      echo "valvoja: ei etenemistä ${age}s, käynnistetään uudelleen" >> "$OUT/run.log"
      kill "$PID"; sleep 3; kill -9 "$PID" 2>/dev/null
    fi
  done
  wait "$PID"; rc=$?
  if grep -q "^Valmis:" <(tail -1 "$OUT/run.log"); then exit 0; fi
  [ "$rc" -eq 0 ] && exit 0
done
