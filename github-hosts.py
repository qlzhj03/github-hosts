#!/usr/bin/env python3
"""Pin verified GitHub IPs in /etc/hosts to bypass polluted DNS answers.

Usage:
  python3 github-hosts.py check     # dry run, only probe and report
  sudo python3 github-hosts.py apply   # probe and update /etc/hosts
  sudo python3 github-hosts.py restore # remove the managed block

Only the block between the BEGIN/END markers is ever touched.
raw.githubusercontent.com is intentionally NOT pinned: its TCP port is
reachable but TLS is reset by SNI-based filtering, so no IP can fix it.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

BEGIN = "# BEGIN github-hosts"
END = "# END github-hosts"
LEGACY_BLOCKS = (("# BEGIN WORKBUDDY-GITHUB", "# END WORKBUDDY-GITHUB"),)
HOSTS_FILE = "/etc/hosts"
BACKUP_PREFIX = "/etc/hosts.github-hosts-backup-"
BACKUP_KEEP = 10
PROBE_ATTEMPTS = 3

# gist.github.com is served by the same frontend as github.com, so a healthy
# github.com IP is a valid gist candidate when its own DNS answers are polluted.
ALIASES = {"gist.github.com": "github.com"}

# Seed IPs observed healthy during diagnosis, plus live DoH answers.
SEEDS = {
    "github.com": ["20.205.243.166", "20.247.166.64", "20.6.7.17"],
    "www.github.com": ["20.205.243.166", "20.247.166.64"],
    "api.github.com": ["20.205.243.168"],
    "codeload.github.com": ["20.205.243.165", "20.247.166.65", "20.6.7.16"],
    "gist.github.com": ["20.205.243.166", "20.247.166.64"],
    "github.githubassets.com": [
        "185.199.108.215", "185.199.109.215",
        "185.199.110.215", "185.199.111.215",
    ],
    "avatars.githubusercontent.com": [
        "185.199.108.133", "185.199.109.133",
        "185.199.110.133", "185.199.111.133",
    ],
    "camo.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "objects.githubusercontent.com": [
        "185.199.108.133", "185.199.109.133",
        "185.199.110.133", "185.199.111.133",
    ],
    "media.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "user-images.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
    "gist.githubusercontent.com": ["185.199.108.133", "185.199.109.133"],
}

DOH_ENDPOINTS = (
    "https://dns.alidns.com/resolve?name={name}&type=A",
    "https://doh.pub/resolve?name={name}&type=A",
)

IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def valid_ip(ip):
    if not IPV4_RE.match(ip):
        return False
    if ip.startswith(("0.", "127.", "169.254.", "255.")):
        return False
    return all(0 <= int(part) <= 255 for part in ip.split("."))


def doh_lookup(domain):
    ips = set()
    for endpoint in DOH_ENDPOINTS:
        try:
            req = urllib.request.Request(
                endpoint.format(name=domain),
                headers={"User-Agent": "github-hosts/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.load(resp)
            for answer in data.get("Answer", []):
                ip = answer.get("data", "")
                if answer.get("type") == 1 and valid_ip(ip):
                    ips.add(ip)
        except Exception:
            continue
    return ips


def probe_once(domain, ip):
    """Return latency in seconds if the IP serves this domain, else None."""
    cmd = [
        "curl", "-4", "-sS", "-o", "/dev/null",
        "--max-time", "5",
        "--resolve", "%s:443:%s" % (domain, ip),
        "-w", "%{http_code} %{time_total}",
        "https://%s/" % domain,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.split()
    if len(parts) != 2 or parts[0] == "000":
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


def probe(domain, ip):
    """Retry a few times: a single TLS reset under filtering is not conclusive."""
    for _ in range(PROBE_ATTEMPTS):
        latency = probe_once(domain, ip)
        if latency is not None:
            return latency
    return None


def pick_best(domain, extra=None):
    candidates = set(SEEDS.get(domain, []))
    if extra:
        candidates |= {ip for ip in extra if valid_ip(ip)}
    candidates |= doh_lookup(domain)
    candidates = {ip for ip in candidates if valid_ip(ip)}
    if not candidates:
        return None, []
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe, domain, ip): ip for ip in candidates}
        for fut in concurrent.futures.as_completed(futures):
            ip = futures[fut]
            latency = fut.result()
            if latency is not None:
                results.append((latency, ip))
    results.sort()
    best = results[0][1] if results else None
    return best, results


def read_managed_block(text):
    """Return {domain: ip} from current and legacy managed blocks."""
    begins = {BEGIN} | {begin for begin, _ in LEGACY_BLOCKS}
    ends = {END} | {end for _, end in LEGACY_BLOCKS}
    current = {}
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in begins:
            in_block = True
            continue
        if stripped in ends:
            in_block = False
            continue
        if in_block:
            parts = line.split()
            if len(parts) >= 2 and valid_ip(parts[0]):
                current[parts[1]] = parts[0]
    return current


def remove_block(lines, begin, end):
    """Remove one marker pair; keep everything if the block is unterminated."""
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == begin:
            close = None
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == end:
                    close = j
                    break
            if close is None:
                out.append(lines[i])
                i += 1
                continue
            i = close + 1
            continue
        out.append(lines[i])
        i += 1
    return out


def strip_managed_block(text):
    lines = text.splitlines()
    for begin, end in ((BEGIN, END),) + LEGACY_BLOCKS:
        lines = remove_block(lines, begin, end)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines


def write_hosts(new_text):
    directory = os.path.dirname(HOSTS_FILE)
    fd, tmp = tempfile.mkstemp(prefix=".hosts.github-hosts-", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(new_text)
        shutil.copymode(HOSTS_FILE, tmp)
        os.replace(tmp, HOSTS_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def prune_backups():
    prefix = os.path.basename(BACKUP_PREFIX)
    backups = sorted(
        name for name in os.listdir("/etc") if name.startswith(prefix)
    )
    for name in backups[:-BACKUP_KEEP]:
        try:
            os.unlink(os.path.join("/etc", name))
        except OSError:
            pass


def alias_candidates(domain, chosen):
    alias = ALIASES.get(domain)
    if alias and alias in chosen:
        return [chosen[alias]]
    return None


def cmd_check():
    failed = []
    chosen = {}
    for domain in SEEDS:
        best, results = pick_best(domain, alias_candidates(domain, chosen))
        if best:
            chosen[domain] = best
            healthy = ", ".join("%s(%.2fs)" % (ip, lat) for lat, ip in results[:3])
            print("[OK]   %-36s -> %s | healthy: %s" % (domain, best, healthy))
        else:
            failed.append(domain)
            print("[FAIL] %-36s -> no healthy candidate" % domain)
    print()
    print("Note: raw.githubusercontent.com is SNI-blocked (TLS reset); use a mirror or proxy.")
    return 1 if failed else 0


def cmd_apply():
    if os.geteuid() != 0:
        print("apply needs root: sudo python3 %s apply" % __file__)
        return 2
    with open(HOSTS_FILE, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()
    previous = read_managed_block(original)

    lines = [BEGIN]
    chosen = {}
    failed = []
    for domain in SEEDS:
        best, results = pick_best(domain, alias_candidates(domain, chosen))
        if best:
            chosen[domain] = best
            lines.append("%s %s" % (best, domain))
            print("[OK]   %-36s -> %s" % (domain, best))
        elif domain in previous:
            chosen[domain] = previous[domain]
            lines.append("%s %s" % (previous[domain], domain))
            print("[KEEP] %-36s -> %s (probe failed, keep last good)" % (domain, previous[domain]))
        else:
            failed.append(domain)
            print("[SKIP] %-36s (no healthy candidate, left to system DNS)" % domain)
    lines.append(END)

    body = strip_managed_block(original)
    new_text = "\n".join(body + [""] + lines) + "\n"
    if new_text == original:
        print("No change needed.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_PREFIX + stamp
    shutil.copy2(HOSTS_FILE, backup)
    write_hosts(new_text)
    prune_backups()
    print()
    print("Updated %s (%d domains). Backup: %s" % (HOSTS_FILE, len(chosen), backup))
    if failed:
        print("Unresolved domains: %s" % ", ".join(failed))
    return 0


def cmd_restore():
    if os.geteuid() != 0:
        print("restore needs root: sudo python3 %s restore" % __file__)
        return 2
    with open(HOSTS_FILE, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()
    body = strip_managed_block(original)
    new_text = "\n".join(body) + "\n"
    if new_text == original:
        print("Nothing to restore.")
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_PREFIX + stamp
    shutil.copy2(HOSTS_FILE, backup)
    write_hosts(new_text)
    print("Managed block removed. Backup: %s" % backup)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "apply", "restore"])
    args = parser.parse_args()
    if not shutil.which("curl"):
        print("curl is required but not found in PATH")
        return 2
    return {"check": cmd_check, "apply": cmd_apply, "restore": cmd_restore}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
