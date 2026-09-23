# config.py — Parser YAML subset + validasi config lab (blueprint §13, fail-closed)
# Template canonical = configs/lab.example.yaml. Runner memvalidasi schema/version,
# flag isolasi, dan kecocokan hash sebelum bekerja. Tidak ada fallback tersembunyi.

import re
from pathlib import Path
from typing import Any, Dict, List

SCALARS_TRUE = {"true", "yes"}
SCALARS_FALSE = {"false", "no"}


def _parse_scalar(tok: str) -> Any:
    t = tok.strip()
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        return t[1:-1]
    low = t.lower()
    if low in {"null", "~", ""}:
        return None
    if low in SCALARS_TRUE:
        return True
    if low in SCALARS_FALSE:
        return False
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+([eE][-+]?\d+)?", t):
        return float(t)
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x) for x in inner.split(",")]
    return t


def _is_kv(body: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.\-]+\s*:", body))


def _split_kv(body: str):
    key, _, rest = body.partition(":")
    return key.strip(), rest.strip()


def _parse_block(items, i: int, indent: int):
    """Parse blok pada indent tertentu. Return (value, next_index)."""
    first = items[i][1]
    if first.startswith("- "):
        seq: List[Any] = []
        while i < len(items) and items[i][0] == indent and items[i][1].startswith("- "):
            body = items[i][1][2:].strip()
            i += 1
            if not body:
                seq.append(None)
                continue
            if _is_kv(body):
                key, rest = _split_kv(body)
                d: Dict[str, Any] = {}
                if rest:
                    d[key] = _parse_scalar(rest)
                elif i < len(items) and items[i][0] > indent:
                    v, i = _parse_block(items, i, items[i][0])
                    d[key] = v
                else:
                    d[key] = None
                # sisa key item-map berada pada indent+2
                while (i < len(items) and items[i][0] == indent + 2
                       and _is_kv(items[i][1])):
                    k2, rest2 = _split_kv(items[i][1])
                    i += 1
                    if rest2:
                        d[k2] = _parse_scalar(rest2)
                    elif i < len(items) and items[i][0] > indent + 2:
                        v, i = _parse_block(items, i, items[i][0])
                        d[k2] = v
                    else:
                        d[k2] = None
                seq.append(d)
            else:
                seq.append(_parse_scalar(body))
        return seq, i
    d = {}
    while i < len(items) and items[i][0] == indent and _is_kv(items[i][1]):
        key, rest = _split_kv(items[i][1])
        i += 1
        if rest:
            d[key] = _parse_scalar(rest)
        elif i < len(items) and items[i][0] > indent:
            v, i = _parse_block(items, i, items[i][0])
            d[key] = v
        else:
            d[key] = None
    return d, i


def parse_yaml(text: str) -> Dict[str, Any]:
    """Parser YAML subset: indentasi 2-spasi, nested map, list `- `, scalar,
    inline list `[a, b]`. Cukup untuk template §13 yang dibekukan; bukan parser
    YAML umum — template berubah = config_version baru."""
    items = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        items.append((indent, line.strip()))
    if not items:
        return {}
    if items[0][0] != 0:
        raise ValueError("baris pertama harus indent 0")
    value, idx = _parse_block(items, 0, 0)
    if idx != len(items):
        raise ValueError(f"baris tersisa tidak terparse (indent {items[idx][0]}): {items[idx][1]!r}")
    return value


class ConfigError(Exception):
    pass


REQUIRED_KEYS = ("project", "config_version", "mode", "enabled",
                 "isolation", "storage", "run", "research", "selection",
                 "execution", "runtime_budget")


def validate_config(cfg: Dict[str, Any]) -> None:
    for k in REQUIRED_KEYS:
        if k not in cfg:
            raise ConfigError(f"blok wajib hilang: {k}")
    if cfg["project"] != "idxspark-v8-lab":
        raise ConfigError("project harus 'idxspark-v8-lab'")
    if int(cfg["config_version"]) != 1:
        raise ConfigError("config_version tidak didukung")
    if cfg["mode"] not in ("REPLAY", "SHADOW"):
        raise ConfigError("mode harus REPLAY|SHADOW")
    iso = cfg.get("isolation", {})
    for flag in ("direct_production_read", "production_write", "network_egress",
                 "external_notifications", "order_execution", "auto_promotion"):
        if iso.get(flag) is not False:
            raise ConfigError(f"isolation.{flag} WAJIB false (fail-closed)")
    if iso.get("dedicated_worker_required") is not True:
        raise ConfigError("isolation.dedicated_worker_required wajib true (same-host = alternatif terbatas)")
    st = cfg.get("storage", {})
    for k in ("input_root", "output_root", "ledger"):
        if not st.get(k):
            raise ConfigError(f"storage.{k} wajib ada")
    if st.get("input_mount_readonly") is not True:
        raise ConfigError("storage.input_mount_readonly wajib true")
    run = cfg.get("run", {})
    if run.get("single_writer") is not True or run.get("publish_complete_only") is not True:
        raise ConfigError("run.single_writer dan publish_complete_only wajib true")
    sel = cfg.get("selection", {})
    # Parameter null sengaja memblokir SHADOW_QUALIFIED/promosi (blueprint §13)
    for k in ("policy_version", "p_min", "expected_net_min", "total_risk_budget",
              "max_open_positions", "max_sector_exposure"):
        if sel.get(k) is not None:
            raise ConfigError(f"selection.{k} harus null pada template beku 0.1")
    if int(sel.get("baseline_cap_on", 2)) != 2 or int(sel.get("baseline_cap_off", 1)) != 1:
        raise ConfigError("cap baseline beku: ON=2/OFF=1")


def check_scheduled_allowed(cfg: Dict[str, Any]) -> List[str]:
    """Scheduled run (enabled=true / systemd) menolak budget null (fail-closed)."""
    problems = []
    rb = cfg.get("runtime_budget", {})
    for k in ("cpu_limit", "memory_limit_bytes", "max_run_seconds"):
        if rb.get(k) is None:
            problems.append(f"runtime_budget.{k} = null")
    if cfg.get("enabled") is not True:
        problems.append("enabled=false")
    return problems


def load_config(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config tidak ditemukan: {path}")
    cfg = parse_yaml(p.read_text(encoding="utf-8"))
    validate_config(cfg)
    return cfg
