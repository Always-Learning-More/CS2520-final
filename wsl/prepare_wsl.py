#!/usr/bin/env python3
"""Prepare a copy of this repository that came from Windows for use in WSL.

Copying a checkout through Windows (Explorer, a Windows git clone, OneDrive,
zip tools, ...) usually changes it in two ways that break Linux tooling:

  * text files get CRLF line endings, so shell scripts fail with errors such
    as "set: pipefail\\r: invalid option name";
  * executable bits are lost (or every file becomes executable).

This script undoes both. It is written in Python so that it still runs when it
has itself been converted to CRLF. It is safe to run more than once.

    python3 wsl/prepare_wsl.py                      # fix this checkout in place
    python3 wsl/prepare_wsl.py --copy-to ~/OmniPath-Ping
                                                    # copy off /mnt/c first, then fix

If the copy contains .git and git is installed, files are restored to their
exact committed bytes and modes wherever the only difference is line endings
or permissions; genuine edits are kept. Without git, CRLF is converted to LF in
every text file and scripts that start with "#!" are made executable.
"""

import argparse
import hashlib
import os
import pathlib
import shutil
import stat
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "build", "target", "__pycache__"}
BINARY_SUFFIXES = {".pcap", ".png", ".pdf", ".dia", ".gz", ".bz2", ".zip", ".pyc"}
KEEP_CRLF_SUFFIXES = {".bat"}


def is_binary(path, data):
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    # The large "waf" files are Python 2 scripts with an embedded bz2 archive.
    if path.name == "waf" and len(data) > 4096:
        return True
    return b"\0" in data


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".waf")]
        for name in filenames:
            yield pathlib.Path(dirpath) / name


def strip_crlf(path):
    """Convert CRLF to LF in a text file. Returns True if the file changed."""
    if path.suffix.lower() in KEEP_CRLF_SUFFIXES or path.is_symlink():
        return False
    data = path.read_bytes()
    if b"\r\n" not in data or is_binary(path, data):
        return False
    mode = path.stat().st_mode
    path.write_bytes(data.replace(b"\r\n", b"\n"))
    os.chmod(str(path), stat.S_IMODE(mode))
    return True


def make_executable(path):
    mode = path.stat().st_mode
    want = mode | ((mode & 0o444) >> 2)  # add x wherever r is set
    if want != mode:
        os.chmod(str(path), stat.S_IMODE(want))
        return True
    return False


def git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def git_available(root):
    if shutil.which("git") is None or not (root / ".git").exists():
        return False
    return git(root, "rev-parse", "--is-inside-work-tree").returncode == 0


def z_list(text):
    return [p for p in text.split("\0") if p]


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_blobs(root, shas):
    """Return {sha: bytes} for the given blob ids using one git process."""
    proc = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, _ = proc.communicate("".join(s + "\n" for s in shas).encode())
    blobs, pos = {}, 0
    for sha in shas:
        nl = out.index(b"\n", pos)
        size = int(out[pos:nl].split()[2])
        blobs[sha] = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return blobs


def fix_with_git(root):
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "core.filemode", "true")

    staged = git(root, "ls-files", "-s", "-z")
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr)

    mode_fixed = restored = edited_lf = 0
    unresolved = []
    for entry in z_list(staged.stdout):
        meta, rel = entry.split("\t", 1)
        mode, sha = meta.split()[:2]
        path = root / rel
        if mode not in ("100644", "100755") or not path.is_file() or path.is_symlink():
            continue
        # 1. Permissions exactly as committed (Windows copies tend to make
        #    every file 0777, or drop the executable bit everywhere).
        want = 0o755 if mode == "100755" else 0o644
        if stat.S_IMODE(path.stat().st_mode) != want:
            os.chmod(str(path), want)
            mode_fixed += 1
        # 2. Contents: if the only difference from the commit is CRLF, put the
        #    committed bytes back. Comparing raw blob hashes keeps this
        #    independent of .gitattributes and core.autocrlf.
        data = path.read_bytes()
        if blob_sha(data) == sha:
            continue
        lf = data.replace(b"\r\n", b"\n")
        if blob_sha(lf) == sha:
            path.write_bytes(lf)
            restored += 1
        else:
            unresolved.append((path, sha, lf))

    # Files committed with CRLF/mixed endings, or genuinely edited files.
    if unresolved:
        blobs = read_blobs(root, sorted({sha for _, sha, _ in unresolved}))
        for path, sha, lf in unresolved:
            blob = blobs[sha]
            if blob.replace(b"\r\n", b"\n") == lf:
                path.write_bytes(blob)          # same text, committed endings
                restored += 1
            elif b"\r\n" not in blob and strip_crlf(path):
                edited_lf += 1                  # real edit made on Windows

    # 3. Untracked files (e.g. new files you added): CRLF -> LF, 0755 for
    #    "#!" scripts and 0644 for everything else.
    untracked = z_list(git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout)
    for rel in untracked:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        if strip_crlf(path):
            edited_lf += 1
        with path.open("rb") as f:
            want = 0o755 if f.read(2) == b"#!" else 0o644
        if stat.S_IMODE(path.stat().st_mode) != want:
            os.chmod(str(path), want)

    git(root, "update-index", "-q", "--refresh")
    remaining = z_list(git(root, "status", "--porcelain", "-z").stdout)
    print("git mode: permissions fixed on %d files, %d files restored to their committed "
          "bytes, %d edited/untracked files converted to LF"
          % (mode_fixed, restored, edited_lf))
    print("git status now lists %d changed/untracked entries (genuine edits only)"
          % len(remaining))


def fix_without_git(root):
    converted = executable = 0
    for path in iter_files(root):
        if not path.is_file() or path.is_symlink():
            continue
        if strip_crlf(path):
            converted += 1
        with path.open("rb") as f:
            if f.read(2) == b"#!" and make_executable(path):
                executable += 1
    print("plain mode: %d files converted to LF, %d scripts made executable"
          % (converted, executable))


def copy_repo(src, dest):
    dest = dest.expanduser().resolve()
    if dest.exists():
        sys.exit("error: %s already exists; choose a new directory" % dest)
    print("copying %s -> %s ..." % (src, dest))
    shutil.copytree(str(src), str(dest), symlinks=True,
                    ignore=shutil.ignore_patterns("build", "target", "__pycache__", ".waf*"))
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--copy-to", type=pathlib.Path, default=None,
                        help="copy the repository to this new directory first "
                             "(use a path inside WSL, e.g. ~/OmniPath-Ping)")
    parser.add_argument("--no-git", action="store_true",
                        help="ignore .git even if present")
    args = parser.parse_args()

    root = REPO_ROOT
    if args.copy_to is not None:
        root = copy_repo(root, args.copy_to)

    if str(root).startswith("/mnt/") and len(root.parts) > 2 and len(root.parts[2]) == 1:
        print("warning: %s is on the Windows filesystem. ns-3 builds and runs there are "
              "very slow and permissions are unreliable.\n"
              "         Re-run with: python3 wsl/prepare_wsl.py --copy-to ~/OmniPath-Ping\n"
              % root)

    if not args.no_git and git_available(root):
        fix_with_git(root)
    else:
        fix_without_git(root)

    print("\nRepository ready at %s\nNext steps:" % root)
    print("  cd %s" % root)
    print("  wsl/opp.sh build      # first run builds the toolchain image, then ns-3")
    print("  wsl/opp.sh smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
