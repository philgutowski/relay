"""The operator interface (U10, R45): seven verbs, no prompts.

Every verb is a subcommand and none of them asks a question, because the `/relay` skill drives
them and a skill session cannot answer one either. There is no operation that exists only inside
the skill's conversation: what the skill can do, an operator at a terminal can do the same way.

Exit codes are the contract, since a detached runner is read by its exit status before anyone
reads its log: 0 fine, 1 the manifest or the environment is wrong, 2 the run halted and needs a
hand, 3 another runner holds the lease.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import (adapters, contracts, manifest as manifest_module, run as run_module, state, summary,
               tail as tail_module, verify)

EXIT_OK = run_module.EXIT_OK
EXIT_CONFIG = run_module.EXIT_CONFIG
EXIT_HALTED = run_module.EXIT_HALTED
EXIT_LEASE = run_module.EXIT_LEASE


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which is Relay's halted code. A bad command line is a
    configuration problem, so it exits 1 like every other one."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(EXIT_CONFIG)


def build_parser():
    parser = _Parser(prog="relay", description="Run a manifest of independent tasks unattended.")
    verbs = parser.add_subparsers(dest="verb", required=True)

    validate = verbs.add_parser("validate", help="check a manifest and its target repo")
    validate.add_argument("manifest")
    validate.add_argument("--list", action="store_true", dest="list_candidates",
                          help="also print the tracker's candidate tasks")

    run_verb = verbs.add_parser("run", help="run the manifest to completion or to a halt")
    run_verb.add_argument("manifest")
    run_verb.add_argument("--retry-blocked", action="store_true",
                          help="retry tasks whose records read blocked")
    run_verb.add_argument("--detach", action="store_true",
                          help="start the run in its own session, logging to the state "
                               "directory, and return at once")

    status = verbs.add_parser("status", help="print the run status without taking the lease")
    status.add_argument("manifest")

    tail_verb = verbs.add_parser("tail", help="follow the running task's activity, decoded")
    tail_verb.add_argument("manifest")

    summary_verb = verbs.add_parser("summary", help="print the run summary")
    summary_verb.add_argument("manifest")
    summary_verb.add_argument("--json", action="store_true", dest="as_json")

    verify_verb = verbs.add_parser("verify", help="re-run the landing verdict for one task")
    verify_verb.add_argument("manifest")
    verify_verb.add_argument("task_id")

    lease = verbs.add_parser("lease", help="inspect or break the lease")
    lease.add_argument("manifest")
    lease.add_argument("--break", action="store_true", dest="break_lease")
    return parser


def _load(path, out):
    try:
        return manifest_module.load(path), None
    except manifest_module.ManifestError as exc:
        out.write("%s\n" % exc)
        return None, EXIT_CONFIG


def _store_for(manifest, env):
    return state.StateStore(manifest.path, manifest.project.repo, home=env.get("HOME"))


def _adapter_for(manifest, env, out):
    try:
        return adapters.build(manifest, env=env), None
    except adapters.ConfigurationError as exc:
        out.write("%s\n" % exc)
        return None, EXIT_CONFIG


def cmd_validate(args, env, out):
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    result = manifest_module.validate(manifest, env=env)
    for applied in result.defaults_applied:
        out.write("default applied: %s\n" % applied)
    for warning in result.warnings:
        out.write("warning: %s\n" % warning)
    for error in result.errors:
        out.write("error: %s\n" % error)
    if not result.ok:
        out.write("%s is not valid: %d error(s)\n" % (args.manifest, len(result.errors)))
        return EXIT_CONFIG
    out.write("%s is valid: %d task(s), %s adapter, %s mode\n"
              % (args.manifest, len(manifest.tasks), manifest.tracker.adapter, manifest.shipping_mode))
    out.write("closeout may touch: %s\n" % ", ".join(result.allowed_paths))
    if args.list_candidates:
        adapter, failure = _adapter_for(manifest, env, out)
        if failure:
            return failure
        candidates = adapter.candidates()
        if not candidates:
            out.write("no candidate tasks read from the tracker\n")
        for entry in candidates:
            out.write("candidate: %s  %s  [%s]\n"
                      % (entry.get("id"), entry.get("title"), entry.get("status")))
    return EXIT_OK


def cmd_run(args, env, out):
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    result = manifest_module.validate(manifest, env=env)
    if not result.ok:
        for error in result.errors:
            out.write("error: %s\n" % error)
        out.write("refusing to run an invalid manifest; fix it and run validate again\n")
        return EXIT_CONFIG
    adapter, failure = _adapter_for(manifest, env, out)
    if failure:
        return failure
    if getattr(args, "detach", False):
        return _detach(args, manifest, env, out)
    outcome = run_module.run(manifest, adapter=adapter, home=env.get("HOME"), base_env=env,
                             retry_blocked=args.retry_blocked,
                             stream=lambda line: out.write(line + "\n"))
    if outcome.message:
        out.write("%s\n" % outcome.message)
    if outcome.store is not None:
        out.write(summary.render(summary.build(manifest, outcome.store)) + "\n")
    return outcome.exit_code


def _detach(args, manifest, env, out):
    """Start the same `run` in its own session and return. `setsid` does not exist on macOS, so
    the /relay skill had to improvise a wrapper on the first Cratekit run; `start_new_session`
    is the portable form. `caffeinate -i` keeps a Mac awake for the run when it is available."""
    store = _store_for(manifest, env)
    log_path = store.path("runner.log")
    entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "relay_cli.py")
    command = [sys.executable, entry, "run", os.path.abspath(args.manifest)]
    if args.retry_blocked:
        command.append("--retry-blocked")
    if shutil.which("caffeinate"):
        command = ["caffeinate", "-i"] + command
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env)
    out.write("runner detached: pid %d\n" % proc.pid)
    out.write("state: %s\n" % store.dir)
    out.write("runner log: %s\n" % log_path)
    return EXIT_OK


def cmd_status(args, env, out):
    """Reads state and nothing else. It never acquires the lease, so an operator can ask what a
    live run is doing without disturbing it."""
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    store = _store_for(manifest, env)
    raw = store.read()
    if raw is None:
        out.write("no state for %s yet\n" % args.manifest)
        return EXIT_OK
    out.write("status: %s\n" % store.status_word())
    out.write("cursor: %d of %d task(s)\n" % (raw.get("cursor", 0), len(manifest.tasks)))
    lease = store.lease()
    if lease:
        out.write("lease: pid %s on %s\n" % (lease.get("holder_pid"), lease.get("hostname")))
    terminal = store.terminal()
    if terminal:
        out.write("terminal record: %s\n" % terminal.get("run_status"))
        if terminal.get("halt_task"):
            out.write("halted on %s with class %s\n"
                      % (terminal.get("halt_task"), terminal.get("halt_class")))
    for task_id, record in sorted(store.records().items()):
        out.write("  %s %s\n" % (task_id, record.get("status")))
    out.write("state: %s\n" % store.dir)
    return EXIT_OK


def cmd_tail(args, env, out):
    """Follows the run's task logs and prints them decoded, one line per event. Reads state and
    the log files and nothing else, so like `status` it never acquires the lease and can run
    beside a live runner.

    The manifest is loaded but not validated: a reader should still be able to watch a run whose
    manifest was edited since it started, and `_load` already refuses one it cannot parse.
    """
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    store = _store_for(manifest, env)
    try:
        run_status = tail_module.follow(manifest, store, lambda line: out.write(line + "\n"))
    except KeyboardInterrupt:
        # The operator stopping a follower is an ordinary ending, not a fault, and the runner is
        # in its own session so this never reached it (R49).
        out.write("\n")
        return EXIT_OK
    return EXIT_HALTED if run_status == contracts.RUN_HALTED else EXIT_OK


def cmd_summary(args, env, out):
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    store = _store_for(manifest, env)
    data = summary.build(manifest, store)
    if args.as_json:
        out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    else:
        out.write(summary.render(data) + "\n")
    return EXIT_HALTED if data["run_status"] == contracts.RUN_HALTED else EXIT_OK


def cmd_verify(args, env, out):
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    adapter, failure = _adapter_for(manifest, env, out)
    if failure:
        return failure
    store = _store_for(manifest, env)
    record = store.get(args.task_id)
    if record is None:
        out.write("no record for %s in %s\n" % (args.task_id, store.dir))
        return EXIT_CONFIG
    verdict = verify.verify(manifest, record, adapter, scope=verify.SCOPE_FULL, do_fetch=True)
    for name, check in verdict.checks.items():
        out.write("  %-26s %s  %s\n" % (name, check["result"], json.dumps(check["evidence"], sort_keys=True)))
    out.write("%s: %s\n" % (args.task_id, "landed" if verdict.landed else "not landed"))
    return EXIT_OK if verdict.landed else EXIT_HALTED


def cmd_lease(args, env, out):
    manifest, failure = _load(args.manifest, out)
    if failure:
        return failure
    store = _store_for(manifest, env)
    lease = store.lease()
    if args.break_lease:
        store.break_lease()
        out.write("lease broken; it was %s\n" % (json.dumps(lease, sort_keys=True) if lease else "free"))
        return EXIT_OK
    if not lease:
        out.write("lease: free\n")
        return EXIT_OK
    out.write("lease: pid %s on %s, manifest %s, heartbeat %s\n"
              % (lease.get("holder_pid"), lease.get("hostname"), lease.get("manifest"),
                 lease.get("heartbeat_at")))
    return EXIT_LEASE


VERBS = {
    "validate": cmd_validate,
    "run": cmd_run,
    "status": cmd_status,
    "tail": cmd_tail,
    "summary": cmd_summary,
    "verify": cmd_verify,
    "lease": cmd_lease,
}


def main(argv=None, env=None, out=None):
    env = dict(os.environ if env is None else env)
    out = out or sys.stdout
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_CONFIG
    return VERBS[args.verb](args, env, out)
