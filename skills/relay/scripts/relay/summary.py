"""The run summary (U10, R36, R46).

The JSON is the summary; the text is rendered from it, never the other way round. That direction
is the whole point of R46: a later session, the `/relay` skill, and the operator's own eye all
read the same facts, and the text can never say something the JSON does not carry.

Every line the text prints names the JSON field it came from, which `lines()` returns alongside
it, so the two cannot drift without a test noticing.

What a summary is for: an operator who was not watching should learn why a task did not land
without opening a transcript. So every task line carries a class and a cause built from the
evidence on the record, and the checks a human still has to make by hand are listed separately
rather than buried in prose. It points at a class, a cause, and the state directory, and never
at a machine readable file the operator would have to parse to learn anything.
"""
import string

from . import contracts

SCHEMA_VERSION = 1


def _template_fields():
    """Every field name any halt line template can ask for, defaulted to a placeholder.

    Derived from the templates rather than listed by hand. A hand written list goes stale the
    moment a template gains a field, and it fails in the wrong direction when it does: the
    missing key raises inside `format`, the except below swallows it, and the operator gets the
    raw template with its braces still in it instead of a sentence.
    """
    fields = {}
    for template in contracts.HALT_LINES.values():
        for _, field, _, _ in string.Formatter().parse(template):
            if field:
                fields[field] = "?"
    return fields


LINE_FIELD_DEFAULTS = _template_fields()


def line_fields(*sources):
    fields = dict(LINE_FIELD_DEFAULTS)
    for source in sources:
        for key, value in (source or {}).items():
            if value is not None and not isinstance(value, (dict, list)):
                fields[key] = value
    return fields


def cause_line(halt_class, *evidence):
    """The halt class's sentence, filled from whatever evidence the record carries."""
    if not halt_class:
        return None
    template = contracts.HALT_LINES.get(halt_class, halt_class)
    try:
        return template.format(**line_fields(*evidence))
    except (KeyError, IndexError, ValueError):
        return template


def _task_entry(store, record):
    task_id = record.get("id")
    evidence = record.get("halt_evidence") or {}
    landing = {"ref": record.get("landing_ref")} if record.get("landing_ref") else {}
    findings = []
    for finding in record.get("findings") or []:
        findings.append({
            "class": finding.get("class"),
            "line": cause_line(finding.get("class"), finding),
        })
    verify_result = record.get("verify") or {}
    failed = [name for name, check in (verify_result.get("checks") or {}).items()
              if check.get("result") == "fail"]
    return {
        "id": task_id,
        "status": record.get("status"),
        "class": record.get("halt_class"),
        # Weakest source first. The record carries fields a template may also name, most
        # of them written after the halt, so the evidence the raiser recorded has to win.
        "cause": cause_line(record.get("halt_class"), record, landing, evidence),
        "halt_message": record.get("halt_message"),
        "backend": record.get("backend"),
        # Issue #58. The other half of a routing choice the operator can edit between runs.
        # `.get`, because a record written before the field joined RECORD_FIELDS has no key.
        "model": record.get("model"),
        "landing_ref": record.get("landing_ref"),
        "branch": record.get("branch"),
        "closeout": record.get("closeout"),
        "excluded_reason": record.get("excluded_reason"),
        "continued_past": bool(record.get("continued_past")),
        "wall_seconds": record.get("wall_seconds"),
        "active_seconds": record.get("active_seconds"),
        "verify_failed": failed,
        # Round eight #54: beside `findings`, not as a Cause line. The empty findings list is
        # what gets misread on a backend that enforces nothing at launch, and an operator who
        # only reads the summary would otherwise never meet the bound run._unenforced_scalar
        # records. None on a backend that refuses a denied call itself.
        "unenforced_restrictions": record.get("unenforced_restrictions"),
        "findings": findings,
        "log_path": store.path("logs", "%s.stdout.log" % task_id) if task_id else None,
    }


def _pending_checks(entries, run_status, halt_task, halt_class, state_dir):
    """R36's last column: what a human still has to do. Each entry is a kind and a sentence, so
    the skill can group them and the text can print them as a list."""
    checks = []
    for entry in entries:
        task_id = entry["id"]
        if entry["status"] == contracts.STATUS_EXCLUDED:
            checks.append({"kind": "excluded", "task": task_id,
                           "text": "%s was skipped: %s. Run it attended."
                                   % (task_id, entry["excluded_reason"] or "no reason recorded")})
        if entry["status"] == contracts.STATUS_HALTED and entry["continued_past"]:
            # Issue #15. The run stepped over this task, so the halted line at the bottom
            # of this list never names it; it needs its own. The retry the record promises
            # needs the branch named here too: it is what a rerun's own pre-flight will
            # refuse on until the operator deletes it (the resume disposition never does).
            branch_note = (" %s is still in place; delete it first." % entry["branch"]
                          if entry["branch"] else "")
            checks.append({"kind": "continued_past", "task": task_id,
                           "text": "%s halted with class %s and the run continued past it."
                                   "%s Repair by hand, then run again to resume."
                                   % (task_id, entry["class"], branch_note)})
        if entry["status"] == contracts.STATUS_BLOCKED and entry["branch"]:
            checks.append({"kind": "stranded_branch", "task": task_id,
                           "text": "%s left %s in place. Keep or delete it by hand."
                                   % (task_id, entry["branch"])})
        for finding in entry["findings"]:
            if finding["class"] == contracts.BLOCKED_UNRECORDED:
                checks.append({"kind": "unrecorded_blocker", "task": task_id,
                               "text": "%s blocked and its card carries no new comment. Check the "
                                       "card by hand." % task_id})
            elif finding["class"] == contracts.HALT_SKILL_SUBSTITUTION:
                checks.append({"kind": "skill_substitution", "task": task_id,
                               "text": "%s ran a skill without the plugin prefix: %s"
                                       % (task_id, finding["line"])})
            elif finding["class"] == contracts.CLOSEOUT_UNFINISHED:
                checks.append({"kind": "closeout_unfinished", "task": task_id,
                               "text": "%s: the closeout did not print a terminal line. Its "
                                       "tracker write may be incomplete." % task_id})
            elif finding["class"] == contracts.HALT_PATH_GATE:
                checks.append({"kind": "path_gate", "task": task_id,
                               "text": "%s: %s" % (
                                   task_id,
                                   finding.get("detail") or contracts.PATH_GATE_CLAUDE_DIR)})
    if run_status == contracts.RUN_HALTED:
        checks.append({"kind": "halted", "task": halt_task,
                       "text": "the run halted on %s with class %s. Repair by hand, then run "
                               "again to resume. State is in %s" % (halt_task, halt_class, state_dir)})
    return checks


def build(manifest, store):
    """The summary as data. Reads state only; acquires nothing and changes nothing."""
    raw = store.read() or {}
    terminal = raw.get("terminal") or {}
    records = store.records()
    order = [task.id for task in manifest.tasks]
    ordered = [records[task_id] for task_id in order if task_id in records]
    ordered += [record for task_id, record in sorted(records.items()) if task_id not in order]
    entries = [_task_entry(store, record) for record in ordered]
    run_status = store.status_word()
    data = {
        "schema_version": SCHEMA_VERSION,
        "manifest": manifest.path,
        "repo": manifest.project.repo,
        "state_dir": store.dir,
        "run_status": run_status,
        "halt_task": terminal.get("halt_task"),
        "halt_class": terminal.get("halt_class"),
        "cli_version": terminal.get("cli_version"),
        "cli_version_observed": terminal.get("cli_version_observed"),
        "cursor": raw.get("cursor", 0),
        "counts": _counts(entries),
        "tasks": entries,
    }
    data["pending_checks"] = _pending_checks(entries, run_status, data["halt_task"],
                                             data["halt_class"], store.dir)
    return data


def _counts(entries):
    counts = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return counts


def _seconds(entry):
    active = entry.get("active_seconds")
    wall = entry.get("wall_seconds")
    if active is None and wall is None:
        return "not run"
    return "%.0fs active, %.0fs wall" % (active or 0, wall or 0)


def lines(data):
    """The text form as (line, source) pairs. `source` names the JSON field the line came from,
    which is how R46's one direction is kept honest."""
    out = [("relay run %s" % data["run_status"], "run_status")]
    out.append(("manifest: %s" % data["manifest"], "manifest"))
    out.append(("state: %s" % data["state_dir"], "state_dir"))
    if data["halt_class"]:
        out.append(("halted on %s with class %s" % (data["halt_task"], data["halt_class"]),
                    "halt_class"))
    out.append(("", "run_status"))
    for index, entry in enumerate(data["tasks"]):
        source = "tasks[%d]" % index
        head = "%s  %s" % (entry["id"], entry["status"])
        if entry["class"]:
            head += "  [%s]" % entry["class"]
        if entry["backend"]:
            # Both halves of the routing when the record carries both, the CLI alone when it
            # does not. A record that never launched has neither and stays untagged.
            head += "  (%s)" % " ".join(filter(None, (entry["backend"], entry["model"])))
        out.append((head, source + ".status"))
        if entry["cause"]:
            out.append(("    %s" % entry["cause"], source + ".cause"))
        if entry["halt_message"] and entry["halt_message"] != entry["cause"]:
            out.append(("    %s" % entry["halt_message"], source + ".halt_message"))
        if entry["excluded_reason"]:
            out.append(("    %s" % entry["excluded_reason"], source + ".excluded_reason"))
        # A landed task's cause line already names the ref; printing it twice was the first
        # live run's summary.
        if entry["landing_ref"] and entry["class"] != contracts.HALT_LANDED:
            out.append(("    landed at %s" % entry["landing_ref"], source + ".landing_ref"))
        if entry["branch"]:
            out.append(("    branch left in place: %s" % entry["branch"], source + ".branch"))
        if entry["closeout"]:
            out.append(("    closeout: %s" % entry["closeout"], source + ".closeout"))
        if entry["verify_failed"]:
            out.append(("    verify failed: %s" % ", ".join(entry["verify_failed"]),
                        source + ".verify_failed"))
        for finding_index, finding in enumerate(entry["findings"]):
            out.append(("    finding: %s" % finding["line"],
                        "%s.findings[%d].line" % (source, finding_index)))
        # Last of the per task lines, directly under the findings, because an empty findings
        # list is the thing it qualifies. A JSON key with no line here would be invisible on
        # every default CLI path, which is the R46 direction this module exists to keep.
        if entry["unenforced_restrictions"]:
            out.append(("    %s" % entry["unenforced_restrictions"],
                        source + ".unenforced_restrictions"))
        out.append(("    %s" % _seconds(entry), source + ".active_seconds"))
        out.append(("    output: %s" % entry["log_path"], source + ".log_path"))
        out.append(("", source + ".id"))
    if data["pending_checks"]:
        out.append(("check by hand:", "pending_checks"))
        for index, check in enumerate(data["pending_checks"]):
            out.append(("  %s" % check["text"], "pending_checks[%d].text" % index))
    return out


def render(data):
    return "\n".join(line for line, _ in lines(data))
