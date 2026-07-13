#!/usr/bin/env python3
"""Record and verify deterministic feature product validation receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

SCHEMA_VERSION = 1
RECEIPT_KIND = "ft.feature.product-validation"
PROCESS_PATHS = (
    ".ft/process/feature/process.yml",
    ".ft/process/feature/scripts",
)
RECEIPT_RELATIVE_PATH = Path("docs/feature-validation.json")
SNAPSHOT_KEYS = (
    "schema_version",
    "kind",
    "product_root",
    "commands",
    "tools",
    "files",
)


class ReceiptError(ValueError):
    """A deterministic, user-facing receipt error."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _fingerprint(snapshot: dict[str, Any]) -> str:
    content = {key: snapshot[key] for key in SNAPSHOT_KEYS}
    return "sha256:" + hashlib.sha256(_canonical_json(content)).hexdigest()


def _git_paths(root: Path, product_root: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-c",
            "-o",
            "--exclude-standard",
            "-z",
            "--",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReceiptError(
            "não foi possível enumerar os arquivos do produto com git: "
            + (detail or f"exit {result.returncode}")
        )
    paths = {
        os.fsdecode(raw_path) for raw_path in result.stdout.split(b"\0") if raw_path
    }

    def is_validation_input(relative: str) -> bool:
        path = Path(relative)
        if not path.parts:
            return False
        # Reconcile atualiza docs e CHANGELOG após a validação completa. Eles
        # são evidência/saída do ciclo, não entrada executável de test/build.
        if path.parts[0] in {"docs", "state"} or relative == "CHANGELOG.md":
            return False
        if len(path.parts) == 1 and (
            path.suffix == ".log" or path.name.startswith("cycle-")
        ):
            return False
        if path.parts[0] == ".ft":
            return relative == PROCESS_PATHS[0] or relative.startswith(
                PROCESS_PATHS[1] + "/"
            )
        return True

    return sorted(relative for relative in paths if is_validation_input(relative))


def _file_record(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": relative, "type": "missing", "sha256": None}

    executable = bool(metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if stat.S_ISLNK(metadata.st_mode):
        content = os.fsencode(os.readlink(path))
        file_type = "symlink"
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ReceiptError(
                f"symlink fora da raiz ou inválido no escopo: {relative}"
            ) from exc
        if not resolved.is_file():
            raise ReceiptError(f"symlink deve apontar para arquivo regular: {relative}")
        target_content = resolved.read_bytes()
    elif stat.S_ISREG(metadata.st_mode):
        content = path.read_bytes()
        file_type = "file"
    else:
        raise ReceiptError(f"tipo de arquivo não suportado no escopo: {relative}")
    record = {
        "path": relative,
        "type": file_type,
        "executable": executable,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if file_type == "symlink":
        record["target_size"] = len(target_content)
        record["target_sha256"] = hashlib.sha256(target_content).hexdigest()
    return record


def _tool_version(command: list[str]) -> dict[str, object]:
    executable = command[0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {"available": False, "version": None}
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "version": type(exc).__name__}
    output = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()), ""
    )
    return {
        "available": result.returncode == 0,
        "version": output or f"exit {result.returncode}",
    }


def _tool_versions() -> dict[str, dict[str, object]]:
    return {
        "bash": _tool_version(["bash", "--version"]),
        "git": _tool_version(["git", "--version"]),
        "make": _tool_version(["make", "--version"]),
        "node": _tool_version(["node", "--version"]),
        "npm": _tool_version(["npm", "--version"]),
        "python": _tool_version([sys.executable, "--version"]),
    }


def _normalize_product_root(root: Path, raw_product_root: str) -> str:
    candidate = Path(raw_product_root)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReceiptError("product_root deve ser relativo à raiz do projeto")
    normalized = candidate.as_posix().strip("/")
    if not normalized or not (root / normalized / "Makefile").is_file():
        raise ReceiptError(
            f"Makefile ausente em product_root: {normalized or raw_product_root}"
        )
    return normalized


def _commands(product_root: str) -> list[list[str]]:
    make = ["env", "-u", "MAKEFLAGS", "-u", "MFLAGS", "-u", "GNUMAKEFLAGS", "make"]
    return [
        [*make, "-C", product_root, "test"],
        [*make, "-C", product_root, "build"],
    ]


def _snapshot(root: Path, raw_product_root: str) -> dict[str, Any]:
    product_root = _normalize_product_root(root, raw_product_root)
    paths = _git_paths(root, product_root)
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "product_root": product_root,
        "commands": _commands(product_root),
        "tools": _tool_versions(),
        "files": [_file_record(root, relative) for relative in paths],
    }
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot


def _receipt_path(root: Path, raw_path: str, product_root: str) -> Path:
    requested = Path(raw_path)
    path = requested if requested.is_absolute() else root / requested
    # ``abspath`` normaliza lexicalmente sem seguir symlinks. Nunca devolvemos
    # o alvo resolvido: invalidate deve apagar somente o receipt, jamais o alvo
    # de um link controlado por uma entrega defeituosa/maliciosa.
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise ReceiptError(
            "o receipt deve permanecer dentro da raiz do projeto"
        ) from exc
    if relative != RECEIPT_RELATIVE_PATH:
        raise ReceiptError(
            f"o receipt deve usar exatamente {RECEIPT_RELATIVE_PATH.as_posix()}"
        )
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReceiptError(f"receipt/pai não pode ser symlink: {current}")
    return absolute


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptError(f"receipt ausente: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"receipt inválido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("receipt deve conter um objeto JSON")
    return payload


def _changed_paths(stored: dict[str, Any], current: dict[str, Any]) -> list[str]:
    def by_path(payload: dict[str, Any]) -> dict[str, object]:
        files = payload.get("files")
        if not isinstance(files, list):
            return {}
        return {
            str(item.get("path")): item
            for item in files
            if isinstance(item, dict) and item.get("path")
        }

    before = by_path(stored)
    after = by_path(current)
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def command_fingerprint(args: argparse.Namespace) -> None:
    snapshot = _snapshot(args.root, args.product_root)
    print(snapshot["fingerprint"])


def command_invalidate(args: argparse.Namespace) -> None:
    product_root = _normalize_product_root(args.root, args.product_root)
    path = _receipt_path(args.root, args.receipt, product_root)
    path.unlink(missing_ok=True)


def command_record(args: argparse.Namespace) -> None:
    snapshot = _snapshot(args.root, args.product_root)
    if snapshot["fingerprint"] != args.expected:
        raise ReceiptError(
            "os arquivos, ferramentas ou comandos mudaram durante a validação completa; rode full novamente"
        )
    path = _receipt_path(args.root, args.receipt, snapshot["product_root"])
    payload = {
        **snapshot,
        "result": "pass",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_receipt(path, payload)
    print(f"product validation receipt PASS: {path.relative_to(args.root)}")


def command_verify(args: argparse.Namespace) -> None:
    product_root = _normalize_product_root(args.root, args.product_root)
    path = _receipt_path(args.root, args.receipt, product_root)
    stored = _load_receipt(path)
    if (
        stored.get("schema_version") != SCHEMA_VERSION
        or stored.get("kind") != RECEIPT_KIND
    ):
        raise ReceiptError("schema/kind do receipt não é suportado")
    if stored.get("result") != "pass":
        raise ReceiptError("receipt não registra uma validação completa PASS")
    if any(key not in stored for key in SNAPSHOT_KEYS):
        raise ReceiptError("receipt não contém todo o contexto determinístico")
    if _fingerprint(stored) != stored.get("fingerprint"):
        raise ReceiptError("fingerprint interno do receipt é inválido")

    current = _snapshot(args.root, product_root)
    if current["fingerprint"] != stored.get("fingerprint"):
        changed = _changed_paths(stored, current)
        detail = ", ".join(changed[:8])
        if len(changed) > 8:
            detail += f", … (+{len(changed) - 8})"
        if not detail:
            detail = "ferramentas, versões ou comandos"
        raise ReceiptError(
            "receipt não corresponde ao estado atual do produto: " + detail
        )
    print(f"product validation receipt VERIFIED: {path.relative_to(args.root)}")


def _common_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--root", type=Path, required=True)
    subparser.add_argument("--product-root", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fingerprint = commands.add_parser("fingerprint")
    _common_parser(fingerprint)
    fingerprint.set_defaults(handler=command_fingerprint)

    invalidate = commands.add_parser("invalidate")
    _common_parser(invalidate)
    invalidate.add_argument("--receipt", required=True)
    invalidate.set_defaults(handler=command_invalidate)

    record = commands.add_parser("record")
    _common_parser(record)
    record.add_argument("--receipt", required=True)
    record.add_argument("--expected", required=True)
    record.set_defaults(handler=command_record)

    verify = commands.add_parser("verify")
    _common_parser(verify)
    verify.add_argument("--receipt", required=True)
    verify.set_defaults(handler=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.root = args.root.resolve()
    try:
        args.handler(args)
    except ReceiptError as exc:
        print(f"product validation receipt FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
