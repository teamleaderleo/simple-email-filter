from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from email_filter.analysis_export import export_mailbox_analysis
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
    if store.apply_results_path.exists():
        raise RuntimeError(
            "The saved state already contains move results. Finish or reset that plan "
            "before rebuilding an analysis export."
        )

    previous_summary = store.summary()
    policy_path = args.policy or str(
        previous_summary.get("policyPath") or _default_policy()
    )
    policies = load_policies(policy_path)

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
