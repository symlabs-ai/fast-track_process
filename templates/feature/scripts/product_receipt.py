#!/usr/bin/env python3
"""Record and verify deterministic feature product validation receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

SCHEMA_VERSION = 3
RECEIPT_KINDS = {
    "baseline": "ft.feature.baseline-attestation",
    "implementation": "ft.feature.product-validation",
}
PROCESS_PATHS = (
    ".ft/process/feature/process.yml",
    ".ft/process/feature/scripts",
)
RECEIPT_RELATIVE_PATHS = {
    "baseline": Path("docs/feature-baseline-attestation.json"),
    "implementation": Path("docs/feature-validation.json"),
}
SNAPSHOT_KEYS = (
    "schema_version",
    "kind",
    "product_root",
    "commands",
    "tools",
    "project_identity",
    "validation_contract",
    "external_dependencies",
    "files",
)
RECEIPT_KEYS = (
    "schema_version",
    "kind",
    "validation_kind",
    "product_root",
    "project_identity",
    "commands",
    "file_count",
    "fingerprint",
    "result",
    "recorded_at",
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
    tools = {
        "bash": _tool_version(["bash", "--version"]),
        "git": _tool_version(["git", "--version"]),
        "make": _tool_version(["make", "--version"]),
        "node": _tool_version(["node", "--version"]),
        "npm": _tool_version(["npm", "--version"]),
        "python": _tool_version([sys.executable, "--version"]),
    }
    ft_executable = shutil.which("ft")
    ft_spec = importlib.util.find_spec("ft")
    package_root = (
        Path(next(iter(ft_spec.submodule_search_locations))).resolve()
        if ft_spec is not None and ft_spec.submodule_search_locations
        else None
    )
    package_digest: str | None = None
    if package_root is not None and package_root.is_dir():
        digest = hashlib.sha256()
        for source in sorted(package_root.rglob("*.py")):
            digest.update(source.relative_to(package_root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(source.read_bytes()).digest())
        package_digest = digest.hexdigest()
    tools["ft"] = {
        **_tool_version(["ft", "--version"]),
        "path": str(Path(ft_executable).resolve()) if ft_executable else None,
        "sha256": (
            hashlib.sha256(Path(ft_executable).read_bytes()).hexdigest()
            if ft_executable and Path(ft_executable).is_file()
            else None
        ),
        "package_root": str(package_root) if package_root is not None else None,
        "package_sha256": package_digest,
    }
    return tools


def _project_identity(root: Path) -> dict[str, str | None]:
    def git_value(*args: str) -> str | None:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None

    common = git_value("rev-parse", "--git-common-dir")
    if common and not Path(common).is_absolute():
        common = str((root / common).resolve())
    return {
        "git_common_dir": common,
        "remote_origin": git_value("config", "--get", "remote.origin.url"),
    }


def _normalize_product_root(root: Path, raw_product_root: str) -> str:
    candidate = Path(raw_product_root)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReceiptError("product_root deve ser relativo à raiz do projeto")
    normalized = candidate.as_posix().strip("/")
    product_path = root / normalized
    if not normalized:
        raise ReceiptError("product_root deve ser relativo à raiz do projeto")
    current = root
    for part in Path(normalized).parts:
        current = current / part
        if current.is_symlink():
            raise ReceiptError(f"product_root não pode conter symlink: {normalized}")
    try:
        resolved = product_path.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ReceiptError(
            f"product_root ausente ou fora da raiz do projeto: {normalized}"
        ) from exc
    if not resolved.is_dir() or not (resolved / "Makefile").is_file():
        raise ReceiptError(
            f"Makefile ausente em product_root: {normalized or raw_product_root}"
        )
    return normalized


def _commands(product_root: str) -> list[list[str]]:
    make = ["env", "-u", "MAKEFLAGS", "-u", "MFLAGS", "-u", "GNUMAKEFLAGS", "make"]
    return [
        [*make, "-C", product_root, "build"],
        [*make, "-C", product_root, "test"],
    ]


def _snapshot(
    root: Path,
    raw_product_root: str,
    validation_kind: str,
) -> dict[str, Any]:
    if validation_kind not in RECEIPT_KINDS:
        raise ReceiptError(f"validation_kind inválido: {validation_kind}")
    product_root = _normalize_product_root(root, raw_product_root)
    paths = _git_paths(root, product_root)
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KINDS[validation_kind],
        "product_root": product_root,
        "commands": _commands(product_root),
        "tools": _tool_versions(),
        "project_identity": _project_identity(root),
        "validation_contract": {
            "template": "feature",
            "validation_kind": validation_kind,
            "hermetic": os.environ.get("FT_FEATURE_VALIDATION_HERMETIC") == "1",
        },
        "external_dependencies": sorted(
            item.strip()
            for item in os.environ.get(
                "FT_FEATURE_EXTERNAL_DEPENDENCIES", ""
            ).split(",")
            if item.strip()
        ),
        "files": [_file_record(root, relative) for relative in paths],
    }
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot


def _receipt_path(
    root: Path,
    raw_path: str,
    product_root: str,
    validation_kind: str,
) -> Path:
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
    expected = RECEIPT_RELATIVE_PATHS[validation_kind]
    if relative != expected:
        raise ReceiptError(
            f"o receipt deve usar exatamente {expected.as_posix()}"
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
    snapshot = _snapshot(args.root, args.product_root, args.validation_kind)
    print(snapshot["fingerprint"])


def command_invalidate(args: argparse.Namespace) -> None:
    product_root = _normalize_product_root(args.root, args.product_root)
    path = _receipt_path(
        args.root, args.receipt, product_root, args.validation_kind
    )
    path.unlink(missing_ok=True)


def command_record(args: argparse.Namespace) -> None:
    snapshot = _snapshot(args.root, args.product_root, args.validation_kind)
    if snapshot["fingerprint"] != args.expected:
        raise ReceiptError(
            "os arquivos, ferramentas ou comandos mudaram durante a validação completa; rode full novamente"
        )
    path = _receipt_path(
        args.root,
        args.receipt,
        snapshot["product_root"],
        args.validation_kind,
    )
    payload = {
        "schema_version": snapshot["schema_version"],
        "kind": snapshot["kind"],
        "validation_kind": args.validation_kind,
        "product_root": snapshot["product_root"],
        "project_identity": snapshot["project_identity"],
        "commands": snapshot["commands"],
        "file_count": len(snapshot["files"]),
        "fingerprint": snapshot["fingerprint"],
        "result": "pass",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_receipt(path, payload)
    print(f"product validation receipt PASS: {path.relative_to(args.root)}")


def command_verify(args: argparse.Namespace) -> None:
    product_root = _normalize_product_root(args.root, args.product_root)
    path = _receipt_path(
        args.root, args.receipt, product_root, args.validation_kind
    )
    stored = _load_receipt(path)
    if set(stored) != set(RECEIPT_KEYS):
        raise ReceiptError("receipt compacto contém campos ausentes ou não permitidos")
    if (
        stored.get("schema_version") != SCHEMA_VERSION
        or stored.get("kind") != RECEIPT_KINDS[args.validation_kind]
        or stored.get("validation_kind") != args.validation_kind
    ):
        raise ReceiptError("schema/kind do receipt não é suportado")
    if stored.get("result") != "pass":
        raise ReceiptError("receipt não registra uma validação completa PASS")
    file_count = stored.get("file_count")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 0:
        raise ReceiptError("file_count do receipt compacto é inválido")
    fingerprint = stored.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", fingerprint
    ):
        raise ReceiptError("fingerprint do receipt compacto é inválido")
    recorded_at = stored.get("recorded_at")
    if not isinstance(recorded_at, str) or not recorded_at.strip():
        raise ReceiptError("recorded_at do receipt compacto é inválido")
    try:
        datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptError("recorded_at do receipt compacto é inválido") from exc
    if not isinstance(stored.get("commands"), list):
        raise ReceiptError("commands do receipt compacto é inválido")

    current = _snapshot(args.root, product_root, args.validation_kind)
    if (
        stored.get("product_root") != current["product_root"]
        or stored.get("commands") != current["commands"]
        or stored.get("project_identity") != current["project_identity"]
    ):
        raise ReceiptError("fingerprint interno do receipt é inválido")
    if current["fingerprint"] != stored.get("fingerprint"):
        raise ReceiptError(
            "receipt não corresponde ao estado atual do produto: "
            "inputs executáveis, ferramentas ou comandos mudaram"
        )
    if stored.get("file_count") != len(current["files"]):
        raise ReceiptError("fingerprint interno do receipt é inválido")
    print(f"product validation receipt VERIFIED: {path.relative_to(args.root)}")


def _common_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--root", type=Path, required=True)
    subparser.add_argument("--product-root", required=True)
    subparser.add_argument(
        "--validation-kind",
        choices=sorted(RECEIPT_KINDS),
        required=True,
    )


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
