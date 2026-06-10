#!/bin/bash
# forge-self-update.sh — Forge Engine self-update cron
#
# Iterates every context pack registered in the project-context SKILL.md L2 MAP,
# regenerates into a shadow dir, diffs against live, and if structural drift
# (tables added/removed, label flips, column changes) is detected:
#   - Publishes the refreshed pack (if calibration passes)
#   - Alerts Daen with structured drift detail
#   - Halts on calibration failure (keeps last-good)
#
# Structured drift only — metadata timestamps don't trigger alerts.
#
# Usage:
#   forge-self-update.sh                        # run all packs
#   forge-self-update.sh --pack shared-db-schema # single pack
#   forge-self-update.sh --dry-run              # preview only, no writes
#
# Cron: daily at 04:00 Asia/Dubai on AX102
# Safety: read-only generators only. No prod DB writes.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────
SKILL_DIR="/opt/gcg/shared/skills/project-context"
SCRIPTS_DIR="$SKILL_DIR/scripts"
LOG_DIR="/opt/gcg/openclaw-talos/workspace/logs"
LOG="$LOG_DIR/forge-self-update.log"
SHADOW_BASE="/tmp/forge-shadow"
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
FLEET_SENDER="${FLEET_SENDER:-talos}"
DRY_RUN=false
RUN_PACK=""

# Pack registry
declare -A PACK_GENERATOR PACK_OUTPUT PACK_CALIBRATION

PACK_GENERATOR["shared-db-schema"]="$SCRIPTS_DIR/db-schema-generator.py"
PACK_OUTPUT["shared-db-schema"]="/opt/gcg/shared/docs/schema/"
PACK_CALIBRATION["shared-db-schema"]="$SCRIPTS_DIR/calibration-harness.py"

PACK_GENERATOR["superapp-data-model"]="$SCRIPTS_DIR/superapp-data-model-generator.py"
PACK_OUTPUT["superapp-data-model"]="/opt/gcg/shared/docs/superapp-data-model/"
PACK_CALIBRATION["superapp-data-model"]="$SCRIPTS_DIR/calibration-harness-superapp.py"

ALL_PACKS=("shared-db-schema" "superapp-data-model")

# ── Helpers ─────────────────────────────────────────────────────────
log()  { echo "[$TIMESTAMP] $*" | tee -a "$LOG"; }
log_n() { echo "$*" | tee -a "$LOG"; }

alert_daen() {
  local severity="${1:-info}"  # info|drift|halt|label_flip
  local subject="$2"
  local body="$3"
  local prio=5
  local label=""

  case "$severity" in
    drift)      prio=3; label="🔁 ";;
    halt)       prio=1; label="🚫 ";;
    label_flip) prio=1; label="🔴 ";;
  esac

  local msg="${label}${subject}
${body}"
  log "[alert → daen] $subject"
  if [ "$DRY_RUN" != "true" ]; then
    fleet send --from talos --priority "$prio" daen "$msg" 2>&1 | tee -a "$LOG"
  else
    log "[dry-run] would fleet-send daen (prio=$prio): $subject"
  fi
}

# ── Structural analysis (ignores metadata timestamps) ────────────────

# Extract sorted table→label pairs from a pack dir (one pass)
extract_table_labels() {
  local dir="$1"
  grep -Eh '## [^.]+\.[^ ]+  +\[(CANONICAL|DECOY)\]' "$dir"/*.md 2>/dev/null | \
    sed -E 's/^## [^.]+\.([^ ]+)  +\[(CANONICAL|DECOY)\]/\1:\2/' | sort -u || true
}

# Compute structural drift between two pack dirs.
# Returns structured summary text. Sets DRIFT_FOUND=true/false via global.
detect_structural_drift() {
  local pack="$1"
  local live_dir="$2"
  local shadow_dir="$3"
  local live_tables shadow_tables

  # Build sorted table lists
  live_map=$(extract_table_labels "$live_dir" 2>/dev/null || true)
  shadow_map=$(extract_table_labels "$shadow_dir" 2>/dev/null || true)

  live_tables=$(echo "$live_map" | cut -d: -f1 | sort -u)
  shadow_tables=$(echo "$shadow_map" | cut -d: -f1 | sort -u)

  local lines=()
  local drifts=0 label_flips=0

  lines+=("Pack: $pack")
  lines+=("")

  # Tables added (in shadow but not in live)
  local added; added=$(comm -13 <(echo "$live_tables") <(echo "$shadow_tables") 2>/dev/null || true)
  if [ -n "$added" ]; then
    lines+=("➕ TABLES ADDED:")
    while IFS= read -r t; do
      [ -z "$t" ] && continue
      local lbl; lbl=$(echo "$shadow_map" | grep "^${t}:" | cut -d: -f2)
      lines+=("  + $t [$lbl]")
      drifts=$((drifts + 1))
    done <<< "$added"
  fi

  # Tables removed (in live but not in shadow)
  local removed; removed=$(comm -23 <(echo "$live_tables") <(echo "$shadow_tables") 2>/dev/null || true)
  if [ -n "$removed" ]; then
    lines+=("➖ TABLES REMOVED:")
    while IFS= read -r t; do
      [ -z "$t" ] && continue
      local lbl; lbl=$(echo "$live_map" | grep "^${t}:" | cut -d: -f2)
      lines+=("  - $t [$lbl]")
      drifts=$((drifts + 1))
    done <<< "$removed"
  fi

  # Label flips (same table, different label)
  local common; common=$(comm -12 <(echo "$live_tables") <(echo "$shadow_tables") 2>/dev/null || true)
  if [ -n "$common" ]; then
    while IFS= read -r t; do
      [ -z "$t" ] && continue
      local live_lbl shadow_lbl
      live_lbl=$(echo "$live_map" | grep "^${t}:" | cut -d: -f2)
      shadow_lbl=$(echo "$shadow_map" | grep "^${t}:" | cut -d: -f2)
      if [ "$live_lbl" != "$shadow_lbl" ]; then
        lines+=("🔄 $t: $live_lbl → $shadow_lbl")
        drifts=$((drifts + 1))
        label_flips=$((label_flips + 1))
      fi
    done <<< "$common"
  fi

  # Summary
  lines+=("")
  lines+=("Structural drifts: $drifts")
  lines+=("Label flips: $label_flips")

  local summary; summary=$(printf '%s\n' "${lines[@]}")

  DRIFT_FOUND=false
  [ "$drifts" -gt 0 ] && DRIFT_FOUND=true

  echo "$summary"
}

# ── Functional / Behavioral drift (functions, triggers, views, FKs) ───

# Extract function/trigger/view/FK fingerprints from FUNCTIONS_RELATIONS.md
extract_function_count() {
  local dir="$1"
  grep -oP '^\|\s+\| \x60[^\x60]+\x60' "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null | wc -l || echo 0
}

extract_function_signatures() {
  local dir="$1"
  # Extract schema + function name from Functions section
  grep -oP '^\|\s+\|\s+\x60\K[^\x60]+' "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null | sort -u || true
}

extract_trigger_fingerprints() {
  local dir="$1"
  # Extract schema.table.trigger from Triggers section
  grep -oP '^\|\s+\S+\s+\|\s+\x60\K[^\x60]+\x60\s+\|\s+\x60\K[^\x60]+' "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null | \
    sed 's/`//g' | tr -s ' ' | sort -u || true
  # Simpler: just get the Triggers table rows
  local in_triggers=false
  while IFS= read -r line; do
    if echo "$line" | grep -q '^## 2.'; then in_triggers=true; continue; fi
    if echo "$line" | grep -q '^## 3.'; then in_triggers=false; fi
    if $in_triggers && echo "$line" | grep -q '^| [a-z]'; then
      echo "$line" | awk -F'|' '{print $2 "." $3 ":" $4}' | tr -d ' \x60'
    fi
  done < "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null || true
}

extract_view_fingerprints() {
  local dir="$1"
  local in_views=false
  while IFS= read -r line; do
    if echo "$line" | grep -q '^## 3.'; then in_views=true; continue; fi
    if echo "$line" | grep -q '^## 4.'; then in_views=false; fi
    if $in_views && echo "$line" | grep -q '^| [a-z]'; then
      echo "$line" | awk -F'|' '{print $2 ":" $3}' | tr -d ' \x60'
    fi
  done < "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null || true
}

extract_fk_count() {
  local dir="$1"
  grep -c 'FOREIGN KEY' "$dir/FUNCTIONS_RELATIONS.md" 2>/dev/null || echo 0
}

# Compute functional/behavioral drift between two pack dirs.
# Covers functions added/removed, triggers added/removed, views added/removed, FK count changes.
detect_functional_drift() {
  local pack="$1"
  local live_dir="$2"
  local shadow_dir="$3"

  local lines=()
  local drifts=0

  lines+=("Pack: $pack (functional layer)")
  lines+=("")

  # Only shared-db-schema has FUNCTIONS_RELATIONS.md
  if [ ! -f "$live_dir/FUNCTIONS_RELATIONS.md" ] || [ ! -f "$shadow_dir/FUNCTIONS_RELATIONS.md" ]; then
    lines+=("  (no FUNCTIONS_RELATIONS.md in this pack — skipping functional drift)")
    DRIFT_FOUND=false
    printf '%s\n' "${lines[@]}"
    return
  fi

  # Functions
  local live_funcs shadow_funcs
  live_funcs=$(extract_function_signatures "$live_dir")
  shadow_funcs=$(extract_function_signatures "$shadow_dir")

  local added_funcs; added_funcs=$(comm -13 <(echo "$live_funcs") <(echo "$shadow_funcs") 2>/dev/null || true)
  local removed_funcs; removed_funcs=$(comm -23 <(echo "$live_funcs") <(echo "$shadow_funcs") 2>/dev/null || true)

  local fc_add=0 fc_rem=0
  if [ -n "$added_funcs" ]; then
    fc_add=$(echo "$added_funcs" | wc -l)
    lines+=("ƒ FUNCTIONS ADDED: $fc_add")
    echo "$added_funcs" | head -5 | while IFS= read -r f; do [ -n "$f" ] && lines+=("    + $f"); done
    drifts=$((drifts + fc_add))
  fi
  if [ -n "$removed_funcs" ]; then
    fc_rem=$(echo "$removed_funcs" | wc -l)
    lines+=("ƒ FUNCTIONS REMOVED: $fc_rem")
    echo "$removed_funcs" | head -5 | while IFS= read -r f; do [ -n "$f" ] && lines+=("    - $f"); done
    drifts=$((drifts + fc_rem))
  fi

  # Triggers
  local live_triggers shadow_triggers
  live_triggers=$(extract_trigger_fingerprints "$live_dir")
  shadow_triggers=$(extract_trigger_fingerprints "$shadow_dir")

  local added_triggers; added_triggers=$(comm -13 <(echo "$live_triggers") <(echo "$shadow_triggers") 2>/dev/null || true)
  local removed_triggers; removed_triggers=$(comm -23 <(echo "$live_triggers") <(echo "$shadow_triggers") 2>/dev/null || true)

  if [ -n "$added_triggers" ]; then
    local tc_add; tc_add=$(echo "$added_triggers" | wc -l)
    lines+=("⚡ TRIGGERS ADDED: $tc_add")
    drifts=$((drifts + tc_add))
  fi
  if [ -n "$removed_triggers" ]; then
    local tc_rem; tc_rem=$(echo "$removed_triggers" | wc -l)
    lines+=("⚡ TRIGGERS REMOVED: $tc_rem")
    drifts=$((drifts + tc_rem))
  fi

  # Views
  local live_views shadow_views
  live_views=$(extract_view_fingerprints "$live_dir")
  shadow_views=$(extract_view_fingerprints "$shadow_dir")

  local added_views; added_views=$(comm -13 <(echo "$live_views") <(echo "$shadow_views") 2>/dev/null || true)
  local removed_views; removed_views=$(comm -23 <(echo "$live_views") <(echo "$shadow_views") 2>/dev/null || true)

  if [ -n "$added_views" ]; then
    local vc_add; vc_add=$(echo "$added_views" | wc -l)
    lines+=("🔍 VIEWS ADDED: $vc_add")
    drifts=$((drifts + vc_add))
  fi
  if [ -n "$removed_views" ]; then
    local vc_rem; vc_rem=$(echo "$removed_views" | wc -l)
    lines+=("🔍 VIEWS REMOVED: $vc_rem")
    drifts=$((drifts + vc_rem))
  fi

  # FK count
  local live_fk shadow_fk
  live_fk=$(extract_fk_count "$live_dir")
  shadow_fk=$(extract_fk_count "$shadow_dir")
  if [ "$live_fk" != "$shadow_fk" ]; then
    lines+=("🔗 FK RELATIONSHIPS: $live_fk → $shadow_fk")
    drifts=$((drifts + 1))
  fi

  # Summary
  lines+=("")
  lines+=("Functional/behavioral drifts: $drifts")

  local summary; summary=$(printf '%s\n' "${lines[@]}")

  DRIFT_FOUND=false
  [ "$drifts" -gt 0 ] && DRIFT_FOUND=true

  echo "$summary"
}

# ── Pack lifecycle ──────────────────────────────────────────────────

run_one_pack() {
  local pack="$1"
  local generator="${PACK_GENERATOR[$pack]}"
  local output_dir="${PACK_OUTPUT[$pack]}"
  local calibration="${PACK_CALIBRATION[$pack]}"
  local shadow_dir="$SHADOW_BASE/$pack/$TIMESTAMP"

  # Safety checks
  if [ ! -f "$generator" ]; then
    log "❌ generator not found for pack '$pack': $generator"
    return 1
  fi
  if [ ! -d "$output_dir" ]; then
    log "⚠️  output dir missing for $pack (first run? creating)"
    mkdir -p "$output_dir"
  fi

  log "▶️  Processing pack: $pack"

  # ── Step 1: Backup live and generate to shadow ──
  mkdir -p "$SHADOW_BASE/$pack"
  local backup_dir="$SHADOW_BASE/$pack/backup-$TIMESTAMP"
  mkdir -p "$backup_dir"
  cp -a "$output_dir"/. "$backup_dir"/ 2>/dev/null || true

  log "  Running generator..."
  set +e
  gen_output=$(python3 "$generator" 2>&1)
  gen_result=$?
  set -e

  if [ $gen_result -ne 0 ]; then
    log "❌ Generator execution FAILED for $pack"
    echo "$gen_output" | while IFS= read -r line; do log "  $line"; done
    cp -a "$backup_dir"/. "$output_dir"/ 2>/dev/null || true
    alert_daen "halt" "Generator execution failed for $pack" \
      "The generator script returned exit code $gen_result.\nPrevious output restored from backup.\nLog: $LOG"
    rm -rf "$backup_dir"
    return 1
  fi

  # Copy generated output to shadow dir
  mkdir -p "$shadow_dir"
  cp -a "$output_dir"/. "$shadow_dir"/ 2>/dev/null || true

  # Restore live dir from backup (before calibration)
  rm -rf "$output_dir"/*
  cp -a "$backup_dir"/. "$output_dir"/ 2>/dev/null || true
  rm -rf "$backup_dir"

  # ── Step 2: Detect drift (structural + functional/behavioral) ──
  log "  Checking for drift..."
  local drift_summary=""
  local has_any_drift=false
  local label_flip_count=0

  # Structural drift (tables, labels)
  DRIFT_FOUND=false
  local struct_drift
  struct_drift=$(detect_structural_drift "$pack" "$output_dir" "$shadow_dir")
  if [ "$DRIFT_FOUND" = "true" ]; then
    has_any_drift=true
    label_flip_count=$(echo "$struct_drift" | grep "Label flips:" | grep -oP '\d+$' || echo "0")
    drift_summary+="$struct_drift"$'\n\n'
  fi

  # Functional drift (functions, triggers, views, FKs)
  DRIFT_FOUND=false
  local func_drift
  func_drift=$(detect_functional_drift "$pack" "$output_dir" "$shadow_dir")
  if [ "$DRIFT_FOUND" = "true" ]; then
    has_any_drift=true
    drift_summary+="$func_drift"$'\n'
  fi

  if [ "$has_any_drift" != "true" ]; then
    log "  ✓ No drift detected for $pack"
    rm -rf "$shadow_dir"
    return 0
  fi

  # ⚠️  Drift confirmed
  log "  ⚠️  DRIFT detected"
  echo "$drift_summary" | while IFS= read -r line; do log "    $line"; done

  if [ "$DRY_RUN" = "true" ]; then
    log "  [dry-run] would deploy and calibrate. Summary above."
    rm -rf "$shadow_dir"
    return 0
  fi

  # ── Step 3: Deploy shadow to live (write-if-changed) ──
  log "  Deploying refreshed pack (write-if-changed)..."
  local deploy_backup="$SHADOW_BASE/$pack/deploy-backup-$TIMESTAMP"
  mkdir -p "$deploy_backup"
  cp -a "$output_dir"/. "$deploy_backup"/ 2>/dev/null || true

  local changed_count=0
  local unchanged_count=0
  for f in "$shadow_dir"/*.md; do
    [ -f "$f" ] || continue
    local base; base=$(basename "$f")
    local live_file="$output_dir/$base"
    if [ -f "$live_file" ] && diff -q "$f" "$live_file" >/dev/null 2>&1; then
      unchanged_count=$((unchanged_count + 1))
    else
      cp "$f" "$output_dir/$base"
      changed_count=$((changed_count + 1))
      log "  ~ updated: $base"
    fi
  done
  log "  $changed_count changed, $unchanged_count unchanged (no mtime bump)"

  # ── Step 4: Run calibration harness ──
  log "  Running calibration harness..."
  set +e
  calib_output=$(python3 "$calibration" --verbose 2>&1)
  calib_result=$?
  set -e

  # Extract F1 score
  local f1_score; f1_score=$(echo "$calib_output" | grep -oP 'F1=\K[0-9.]+' || echo "?")
  local severity="drift"
  [ "$label_flip_count" -gt 0 ] && severity="label_flip"

  if [ $calib_result -eq 0 ]; then
    log "  ✅ Calibration PASSED (F1=$f1_score)"
    echo "$calib_output" | while IFS= read -r line; do log "    $line"; done

    alert_daen "$severity" "$pack drift resolved — F1=$f1_score ($label_flip_count label flips)" \
      "Drift auto-detected and published.

$(echo "$drift_summary" | sed 's/^/  /')

Calibration: PASS (F1=$f1_score)
Published: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

    rm -rf "$shadow_dir" "$deploy_backup"
    log "  ✅ $pack published (F1=$f1_score)"
  else
    log "  🚫 Calibration FAILED (F1=$f1_score) — HALTING"
    echo "$calib_output" | while IFS= read -r line; do log "  $line"; done

    # Restore last-good
    rm -rf "$output_dir"/*
    cp -a "$deploy_backup"/. "$output_dir"/ 2>/dev/null || true

    alert_daen "halt" "HALT: Calibration FAILED for $pack (F1=$f1_score)" \
      "Last-good pack restored. Shadow retained for forensics at: $shadow_dir

Calibration output:
$(echo "$calib_output" | sed 's/^/  /')

Triggering drift:
$(echo "$drift_summary" | sed 's/^/  /')"

    rm -rf "$deploy_backup"
    log "  🚫 HALT. Retained shadow: $shadow_dir"
    return 1
  fi
}

# ── Main ────────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift;;
    --pack)    RUN_PACK="$2"; shift 2;;
    *)         echo "Usage: $0 [--pack <name>] [--dry-run]"; exit 1;;
  esac
done

# ── Cull old shadow dirs (keep last 3 per pack) ──
for pack in "${ALL_PACKS[@]}"; do
  pack_shadow="$SHADOW_BASE/$pack"
  if [ -d "$pack_shadow" ]; then
    # list by name (timestamp-sorted), remove all but last 3
    ls -1t "$pack_shadow" 2>/dev/null | tail -n +4 | while IFS= read -r old; do
      rm -rf "$pack_shadow/$old"
      log "  pruned old shadow: $pack/$old"
    done
  fi
done

mkdir -p "$LOG_DIR"
log_n ""
log "═══════════════════════════════════════════════════════════════"
log "🔨 Forge Self-Update starting $(date -u)"

OVERALL_EXIT=0

if [ -n "$RUN_PACK" ]; then
  if [ -z "${PACK_GENERATOR[$RUN_PACK]:-}" ]; then
    log "❌ Unknown pack '$RUN_PACK'. Valid: ${ALL_PACKS[*]}"
    exit 1
  fi
  PACKS_TO_RUN=("$RUN_PACK")
else
  PACKS_TO_RUN=("${ALL_PACKS[@]}")
fi

for pack in "${PACKS_TO_RUN[@]}"; do
  if ! run_one_pack "$pack"; then
    OVERALL_EXIT=1
  fi
done

if [ "$OVERALL_EXIT" -eq 0 ]; then
  log "✅ Forge Self-Update complete — all packs processed"
else
  log "⚠️  Forge Self-Update finished with errors"
fi

exit $OVERALL_EXIT
