import json


def _load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def is_allowed(repo, config_path):
    """Return True only if `repo` ("owner/name") is explicitly allowed and not denied.

    Fail-closed by construction: any config problem (missing file, malformed
    JSON, empty allow-list, missing/empty deny-list), a malformed repo string,
    or a deny match returns False. This gate is the only boundary keeping the
    tool off work repos, so an unconfigured or broken gate must never act.
    """
    try:
        config = _load_config(config_path)
    except (OSError, ValueError):
        return False
    if not isinstance(config, dict):
        return False

    allow = config.get("allow")
    deny = config.get("deny")
    # An empty/missing deny-list means the work-repo guard was never configured.
    if not isinstance(allow, list) or not allow:
        return False
    if not isinstance(deny, list) or not deny:
        return False

    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    owner, _name = parts

    if _denied(owner, repo, deny):
        return False
    return _allowed(owner, repo, allow)


def is_manual_allowed(repo, config_path):
    """Return True only if `repo` ("owner/name") is an exact entry in `manual_allow`.

    The manual-review gate, independent of the auto `allow`/`deny` gate: a repo
    denied for the auto/watch path may still be reviewed by an explicit manual
    run if it is listed here. Fail-closed like `is_allowed` — any config problem,
    a malformed repo string, a glob-shaped entry, or a missing/empty `manual_allow`
    returns False. No owner globs: an entry must be an exact "owner/name".
    """
    try:
        config = _load_config(config_path)
    except (OSError, ValueError):
        return False
    if not isinstance(config, dict):
        return False

    manual = config.get("manual_allow")
    if not isinstance(manual, list) or not manual:
        return False

    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False

    for entry in manual:
        if isinstance(entry, str) and "*" not in entry and entry == repo:
            return True
    return False


def _denied(owner, repo, deny):
    for entry in deny:
        if isinstance(entry, str) and (entry == owner or entry == repo):
            return True
    return False


def _allowed(owner, repo, allow):
    for entry in allow:
        if not isinstance(entry, str):
            continue
        if entry.endswith("/*"):
            if owner == entry[:-2]:
                return True
        elif entry == repo:
            return True
    return False
