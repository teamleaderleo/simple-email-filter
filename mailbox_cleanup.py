from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from email_filter.apply_stages import merge_policy_selection, stage_names
from email_filter.auth import acquire_access_token
from email_filter.graph import GraphClient
from email_filter.historical import (
    APPLY_CONFIRMATION,
    RESET_CONFIRMATION,
    HistoricalMailboxStore,
    build_audit,
    scan_folder,
)
from email_filter.policy import load_policies
from email_filter.review import build_unmatched_review
from email_filter.staged_apply import apply_plan_selection, plan_status

load_dotenv()


def _default_policy() -> str:
    return os.environ.get(
        "MAILBOX_POLICY_PATH",
        "policies/personal.example.json",
    )


def _default_state_dir() -> str:
    return os.environ.get(
        "MAILBOX_STATE_DIR",
        ".mailbox-cleanup/inbox",
    )


def _bounded_apply_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 5000:
        raise argparse.ArgumentTypeError("apply limit must be between 1 and 5000")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _policy_selection(args: argparse.Namespace) -> set[str] | None:
    return merge_policy_selection(
        stage=getattr(args, "stage", None),
        policy_ids=getattr(args, "policy_id", None),
    )


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--stage",
        choices=stage_names(),
        default=None,
        help="Use a named reviewed apply stage.",
    )
    group.add_argument(
        "--policy-id",
        action="append",
        default=None,
        help="Limit the plan to this policy id. Repeat for multiple policies.",
    )


def audit(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    if store.apply_results_path.exists():
        raise RuntimeError(
            "Apply has already started for this state directory. Finish the current "
            "plan or run mailbox-reset before creating a new plan."
        )

    client = GraphClient(acquire_access_token())
    checkpoint = scan_folder(
        client,
        store,
        folder=args.folder,
        page_size=args.page_size,
        max_pages=args.max_pages,
        restart=args.restart,
    )
    policies = load_policies(args.policy)
    summary = build_audit(
        store,
        policies,
        policy_path=str(Path(args.policy)),
        top_limit=args.top,
    )
    summary["checkpointScanned"] = int(checkpoint.get("scanned", 0))
    store.write_summary(summary)
    _print(summary)
    return 0


def replan(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    if store.apply_results_path.exists():
        raise RuntimeError(
            "Apply has already started for this state directory. The saved plan cannot "
            "be replaced until mailbox-reset is run."
        )
    checkpoint = store.checkpoint() or {}
    if not checkpoint.get("complete"):
        raise RuntimeError("Replan requires a complete saved mailbox scan")
    policies = load_policies(args.policy)
    summary = build_audit(
        store,
        policies,
        policy_path=str(Path(args.policy)),
        top_limit=args.top,
    )
    summary["checkpointScanned"] = int(checkpoint.get("scanned", 0))
    store.write_summary(summary)
    _print(summary)
    return 0


def report(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    _print(store.summary())
    return 0


def review(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    summary = store.summary()
    policy_path = args.policy or str(summary.get("policyPath") or _default_policy())
    policies = load_policies(policy_path)
    payload = build_unmatched_review(
        store.load_messages(),
        policies,
        top_senders=args.top,
        samples_per_sender=args.samples,
        sender=args.sender,
        domain=args.domain,
    )
    payload["policyPath"] = policy_path
    payload["stateDir"] = str(Path(args.state_dir))
    _print(payload)
    return 0


def plan(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    payload = plan_status(store, policy_ids=_policy_selection(args))
    payload["stage"] = args.stage
    _print(payload)
    return 0


def apply(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    client = GraphClient(acquire_access_token())
    result = apply_plan_selection(
        client,
        store,
        confirmation=args.confirm,
        limit=args.limit,
        policy_ids=_policy_selection(args),
    )
    result["stage"] = args.stage
    _print(result)
    return 0 if result["failed"] == 0 else 2


def reset(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    store.reset(args.confirm)
    _print({"reset": True, "stateDir": str(Path(args.state_dir))})
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(
            "Audit and gradually clean a large Outlook folder using the shared "
            "retention policy engine."
        )
    )
    root.add_argument(
        "--state-dir",
        default=_default_state_dir(),
        help="Private local checkpoint and report directory.",
    )
    subparsers = root.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit",
        help="Scan or resume a folder and generate a non-destructive report.",
    )
    audit_parser.add_argument("--folder", default="inbox")
    audit_parser.add_argument("--policy", default=_default_policy())
    audit_parser.add_argument("--page-size", type=int, default=999)
    audit_parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Stop after whole pages while preserving a resumable checkpoint.",
    )
    audit_parser.add_argument("--top", type=int, default=25)
    audit_parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart an unapplied scan from the beginning.",
    )
    audit_parser.set_defaults(handler=audit)

    replan_parser = subparsers.add_parser(
        "replan",
        help="Rebuild the local plan from the saved complete snapshot without Graph.",
    )
    replan_parser.add_argument("--policy", default=_default_policy())
    replan_parser.add_argument("--top", type=int, default=25)
    replan_parser.set_defaults(handler=replan)

    report_parser = subparsers.add_parser(
        "report",
        help="Print the latest local audit summary without contacting Graph.",
    )
    report_parser.set_defaults(handler=report)

    review_parser = subparsers.add_parser(
        "review",
        help=(
            "Inspect unmatched senders and redacted subject patterns from the local "
            "snapshot without contacting Graph."
        ),
    )
    review_parser.add_argument(
        "--policy",
        default=None,
        help="Policy file to evaluate. Defaults to the policy recorded by the audit.",
    )
    review_parser.add_argument("--sender", default=None)
    review_parser.add_argument("--domain", default=None)
    review_parser.add_argument("--top", type=_positive_int, default=25)
    review_parser.add_argument("--samples", type=_positive_int, default=4)
    review_parser.set_defaults(handler=review)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Show exact pending and completed counts without contacting Graph.",
    )
    _add_selection_arguments(plan_parser)
    plan_parser.set_defaults(handler=plan)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Move a bounded part of a reviewed plan selection to Deleted Items.",
    )
    apply_parser.add_argument("--limit", type=_bounded_apply_limit, default=500)
    apply_parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {APPLY_CONFIRMATION}.",
    )
    _add_selection_arguments(apply_parser)
    apply_parser.set_defaults(handler=apply)

    reset_parser = subparsers.add_parser(
        "reset",
        help="Delete only the private local snapshot, plan and checkpoints.",
    )
    reset_parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {RESET_CONFIRMATION}.",
    )
    reset_parser.set_defaults(handler=reset)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
