from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3
import requests
from dotenv import load_dotenv

load_dotenv()

import webhook_handler
from email_filter.auth import acquire_access_token
from email_filter.junk_backfill import (
    DELETE_CONFIRMATION,
    RESET_CONFIRMATION,
    JunkBackfillStore,
    apply_plan,
    build_audit_plan,
    delete_message,
    fetch_junk_window,
    get_junk_folder_id,
    get_message_record,
    parse_timestamp,
    plan_status,
)


def _default_state_dir() -> str:
    return os.environ.get("JUNK_BACKFILL_STATE_DIR", ".junk-backfill")


def _bounded_max_messages(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 5000:
        raise argparse.ArgumentTypeError("max messages must be between 1 and 5000")
    return parsed


def _bounded_apply_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000:
        raise argparse.ArgumentTypeError("apply limit must be between 1 and 1000")
    return parsed


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _graph_session() -> tuple[requests.Session, Any]:
    def refresh() -> str:
        return acquire_access_token(force_refresh=True)

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {refresh()}",
            "Accept": "application/json",
            "Prefer": 'IdType="ImmutableId"',
        }
    )
    return session, refresh


def _configure_live_classifier(function_name: str) -> str:
    region = os.environ.get("AWS_REGION", "us-east-2")
    configuration = boto3.client("lambda", region_name=region).get_function_configuration(
        FunctionName=function_name
    )
    variables = ((configuration.get("Environment") or {}).get("Variables") or {})
    required = {
        "CLOUDFLARE_ACCOUNT_ID": str(variables.get("CLOUDFLARE_ACCOUNT_ID") or ""),
        "CLOUDFLARE_API_TOKEN": str(variables.get("CLOUDFLARE_API_TOKEN") or ""),
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise RuntimeError(
            f"The deployed {function_name} Lambda is missing: {', '.join(missing)}"
        )

    webhook_handler.CLOUDFLARE_ACCOUNT_ID = required["CLOUDFLARE_ACCOUNT_ID"]
    webhook_handler.CLOUDFLARE_API_TOKEN = required["CLOUDFLARE_API_TOKEN"]
    webhook_handler.CLOUDFLARE_MODEL = str(
        variables.get("CLOUDFLARE_MODEL")
        or webhook_handler.CLOUDFLARE_MODEL
        or "@cf/google/gemma-4-26b-a4b-it"
    )
    return webhook_handler.CLOUDFLARE_MODEL


def audit(args: argparse.Namespace) -> int:
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    if end <= start:
        raise RuntimeError("Backfill end must be later than start")

    store = JunkBackfillStore(args.state_dir)
    model = _configure_live_classifier(args.function_name)
    session, refresh = _graph_session()
    junk_id = get_junk_folder_id(session, token_refresher=refresh)
    messages, truncated = fetch_junk_window(
        session,
        junk_id,
        start=start,
        end=end,
        token_refresher=refresh,
        max_messages=args.max_messages,
        page_size=args.page_size,
    )

    def progress(record: dict[str, Any]) -> None:
        outcome = "DELETE" if record["shouldDelete"] else "KEEP"
        print(
            f"{record.get('receivedAt') or 'unknown time'} "
            f"{record.get('sender') or 'unknown sender'}: "
            f"{outcome} ({record.get('decision')})"
        )

    plan, summary = build_audit_plan(
        messages,
        start=start,
        end=end,
        model=model,
        classifier=webhook_handler.get_deletion_decision,
        already_processed=webhook_handler.already_processed,
        truncated=truncated,
        progress=progress,
    )
    plan["truncated"] = truncated
    store.write_plan(plan)
    summary["stateDir"] = str(Path(args.state_dir))
    store.write_summary(summary)
    _print(summary)
    if truncated:
        print(
            "ERROR: The window exceeded JUNK_BACKFILL_MAX_MESSAGES. "
            "Increase the cap and rerun the audit before apply."
        )
        return 2
    return 0


def report(args: argparse.Namespace) -> int:
    store = JunkBackfillStore(args.state_dir)
    payload = store.summary()
    payload["apply"] = plan_status(store)
    _print(payload)
    return 0


def apply(args: argparse.Namespace) -> int:
    store = JunkBackfillStore(args.state_dir)
    saved_plan = store.plan()
    if saved_plan.get("truncated") is True:
        raise RuntimeError(
            "Apply refuses a truncated Junk audit. Increase the audit message cap and "
            "rerun it before deleting anything."
        )

    session, refresh = _graph_session()
    junk_id = get_junk_folder_id(session, token_refresher=refresh)

    def delete_one(message_id: str) -> str:
        try:
            return delete_message(session, message_id, token_refresher=refresh)
        except Exception as exc:
            print(f"Delete failed for one planned Junk message: {exc}")
            raise

    result = apply_plan(
        store,
        confirmation=args.confirm,
        junk_folder_id=junk_id,
        get_message=lambda message_id: get_message_record(
            session,
            message_id,
            token_refresher=refresh,
        ),
        delete=delete_one,
        already_processed=webhook_handler.already_processed,
        mark_processed=webhook_handler.mark_processed,
        limit=args.limit,
    )
    _print(result)
    return 0 if result["failed"] == 0 else 2


def reset(args: argparse.Namespace) -> int:
    store = JunkBackfillStore(args.state_dir)
    store.reset(args.confirm)
    _print({"reset": True, "stateDir": str(Path(args.state_dir))})
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(
            "Audit and replay a bounded Junk Email notification gap using the live "
            "Junk Guard rules and Gemma classifier."
        )
    )
    root.add_argument(
        "--state-dir",
        default=_default_state_dir(),
        help="Private local plan and apply-result directory.",
    )
    subparsers = root.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit",
        help="Classify messages still in Junk during a bounded time window; delete none.",
    )
    audit_parser.add_argument("--start", required=True)
    audit_parser.add_argument("--end", required=True)
    audit_parser.add_argument("--max-messages", type=_bounded_max_messages, default=500)
    audit_parser.add_argument("--page-size", type=_bounded_max_messages, default=100)
    audit_parser.add_argument(
        "--function-name",
        default=os.environ.get("WEBHOOK_FUNCTION", "email-webhook-handler"),
        help="Deployed Lambda whose private Cloudflare configuration should be reused.",
    )
    audit_parser.set_defaults(handler=audit)

    report_parser = subparsers.add_parser(
        "report",
        help="Print the saved audit and apply status without contacting Microsoft.",
    )
    report_parser.set_defaults(handler=report)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Delete a bounded part of the saved DELETE-only Junk plan.",
    )
    apply_parser.add_argument("--limit", type=_bounded_apply_limit, default=250)
    apply_parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {DELETE_CONFIRMATION}.",
    )
    apply_parser.set_defaults(handler=apply)

    reset_parser = subparsers.add_parser(
        "reset",
        help="Delete only the private local Junk backfill state.",
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
