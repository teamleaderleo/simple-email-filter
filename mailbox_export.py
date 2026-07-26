from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from email_filter.analysis_export import export_mailbox_analysis
from email_filter.apply_progress_export import build_apply_progress, write_apply_progress
from email_filter.historical import HistoricalMailboxStore, build_audit
from email_filter.policy import load_policies

load_dotenv()


def _default_state_dir() -> str:
    return os.environ.get("MAILBOX_STATE_DIR", ".mailbox-cleanup/inbox")


def _default_policy() -> str:
    return os.environ.get("MAILBOX_POLICY_PATH", "policies/personal.example.json")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Create privacy-minimised JSON, CSV and Excel analysis files from an "
            "existing local mailbox snapshot. Microsoft Graph is not contacted."
        )
    )
    result.add_argument("--state-dir", default=_default_state_dir())
    result.add_argument(
        "--policy",
        default=None,
        help="Policy path. Defaults to the path recorded by the latest audit.",
    )
    result.add_argument(
        "--output-dir",
        default=None,
        help="Destination. Defaults to <state-dir>/export.",
    )
    result.add_argument(
        "--samples",
        type=_positive_int,
        default=6,
        help="Redacted subject patterns retained per unmatched sender.",
    )
    result.add_argument(
        "--top",
        type=_positive_int,
        default=100,
        help="Top sender/domain rows retained in the nested summary.",
    )
    return result


def run(args: argparse.Namespace) -> dict:
    store = HistoricalMailboxStore(args.state_dir)
    checkpoint = store.checkpoint() or {}
    if not checkpoint.get("complete"):
        raise RuntimeError(
            "The mailbox scan is incomplete. Finish mailbox-audit before exporting; "
            "rolling retention rules require the complete folder history."
        )

    previous_summary = store.summary()
    recorded_policy_path = str(
        previous_summary.get("policyPath") or _default_policy()
    )
    policy_path = args.policy or recorded_policy_path
    apply_started = store.apply_results_path.exists()

    if apply_started and Path(policy_path) != Path(recorded_policy_path):
        raise RuntimeError(
            "Apply has already started, so export must use the policy recorded by the "
            "saved plan. Reset the local state before changing policies."
        )

    policies = load_policies(policy_path)
    if apply_started:
        # Never rebuild or replace a plan once outcomes have been recorded. The base
        # analysis still reflects the saved snapshot and policy, while progress
        # sidecars report moved, pending, missing and retryable counts.
        summary = previous_summary
    else:
        # Rebuild locally so a changed policy is reflected without another Graph scan.
        summary = build_audit(
            store,
            policies,
            policy_path=policy_path,
            top_limit=args.top,
        )
        summary["checkpointScanned"] = int(checkpoint.get("scanned", 0))
        store.write_summary(summary)

    output_dir = Path(args.output_dir) if args.output_dir else store.root / "export"
    payload = export_mailbox_analysis(
        output_dir,
        store.load_messages(),
        policies,
        summary,
        policy_path=policy_path,
        samples_per_sender=args.samples,
    )

    progress = build_apply_progress(store)
    progress_files = write_apply_progress(output_dir, progress)
    payload["files"].update(progress_files)
    payload["applyProgress"] = {
        "applyStarted": progress["applyStarted"],
        "moved": progress["allPlan"]["moved"],
        "pending": progress["allPlan"]["pending"],
        "missing": progress["allPlan"]["missing"],
        "failedLastAttempt": progress["allPlan"]["failedLastAttempt"],
    }
    payload["stateDir"] = str(store.root)
    return payload


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
