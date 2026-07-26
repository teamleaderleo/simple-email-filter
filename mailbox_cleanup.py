from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from email_filter.auth import acquire_access_token
from email_filter.graph import GraphClient
from email_filter.historical import (
    APPLY_CONFIRMATION,
    RESET_CONFIRMATION,
    HistoricalMailboxStore,
    apply_plan,
    build_audit,
    scan_folder,
)
from email_filter.policy import load_policies

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


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def audit(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
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
    _print(summary)
    return 0


def report(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    _print(store.summary())
    return 0


def apply(args: argparse.Namespace) -> int:
    store = HistoricalMailboxStore(args.state_dir)
    client = GraphClient(acquire_access_token())
    result = apply_plan(
        client,
        store,
        confirmation=args.confirm,
        limit=args.limit,
    )
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

    report_parser = subparsers.add_parser(
        "report",
        help="Print the latest local audit summary without contacting Graph.",
    )
    report_parser.set_defaults(handler=report)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Move a bounded part of the reviewed plan to Deleted Items.",
    )
    apply_parser.add_argument("--limit", type=int, default=500)
    apply_parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {APPLY_CONFIRMATION}.",
    )
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
